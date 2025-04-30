from fastapi import Depends, HTTPException, Query, Form
from starlette.requests import Request
from starlette.responses import RedirectResponse, StreamingResponse
import logging
from bson import ObjectId
from datetime import datetime
import asyncio
import csv
import io

from examples.services.fcm_service import FCMService
from fastapi_admin.app import app
from fastapi_admin.depends import get_resources, get_current_admin
from fastapi_admin.template import templates
from examples.permissions import PermissionDependency


# Configure logging
logger = logging.getLogger(__name__)

@app.get("/showing-requests", dependencies=[Depends(PermissionDependency(["view_showing_requests"]))])
async def showing_requests(
    request: Request,
    resources=Depends(get_resources),
    admin=Depends(get_current_admin),
    page: int = Query(1, ge=1),
    per_page: int = 10,
    agent_id: str = Query(None),
    property_id: str = Query(None),
    status: str = Query(None, regex="^(pending|approved|denied|rescheduled|completed|cancelled|None)$"),
    date_from: str = Query(None),
    date_to: str = Query(None),
    sort_by: str = Query("created_at", regex="^(created_at|requested_datetime|updated_at)$"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        
        # Calculate skip and limit for pagination
        skip = (page - 1) * per_page
        
        # Build match conditions
        match_conditions = {}
            
        if agent_id and agent_id != "None":
            match_conditions["agent_id"] = agent_id
            
        if property_id and property_id != "None":
            match_conditions["property_id"] = ObjectId(property_id)
            
        if status and status != "None":
            match_conditions["status"] = status
            
        if date_from:
            try:
                date_from_dt = datetime.fromisoformat(date_from)
                match_conditions["requested_datetime"] = {"$gte": date_from_dt}
            except ValueError:
                pass
                
        if date_to:
            try:
                date_to_dt = datetime.fromisoformat(date_to)
                if "requested_datetime" in match_conditions:
                    match_conditions["requested_datetime"]["$lte"] = date_to_dt
                else:
                    match_conditions["requested_datetime"] = {"$lte": date_to_dt}
            except ValueError:
                pass
        
        # Create aggregation pipeline
        pipeline = [
            # Match showing requests based on conditions
            {"$match": match_conditions},
            # Lookup property details
            {
                "$lookup": {
                    "from": "properties",
                    "let": { "property_id": "$property_id" },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": { "$eq": ["$_id", "$$property_id"] }
                            }
                        }
                    ],
                    "as": "property"
                }
            },
            # Lookup requester details
            {
                "$lookup": {
                    "from": "users",
                    "let": { "requester_id": "$requester_id" },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": { "$eq": ["$uuid", "$$requester_id"] }
                            }
                        }
                    ],
                    "as": "requester"
                }
            },
            # Lookup seller details
            {
                "$lookup": {
                    "from": "users",
                    "let": { "seller_id": "$seller_id" },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": { "$eq": ["$uuid", "$$seller_id"] }
                            }
                        }
                    ],
                    "as": "seller"
                }
            },
            # Lookup agent details if exists
            {
                "$lookup": {
                    "from": "users",
                    "let": { "agent_id": "$agent_id" },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": { "$eq": ["$uuid", "$$agent_id"] }
                            }
                        }
                    ],
                    "as": "agent"
                }
            },
            # Unwind arrays (we expect one document per lookup)
            {"$unwind": {"path": "$property", "preserveNullAndEmptyArrays": True}},
            {"$unwind": {"path": "$requester", "preserveNullAndEmptyArrays": True}},
            {"$unwind": {"path": "$seller", "preserveNullAndEmptyArrays": True}},
            {"$unwind": {"path": "$agent", "preserveNullAndEmptyArrays": True}},
            # Add computed fields
            {
                "$addFields": {
                    "property_address": "$property.address",
                    "requester_name": {
                        "$concat": [
                            {"$ifNull": ["$requester.first_name", ""]},
                            " ",
                            {"$ifNull": ["$requester.last_name", ""]}
                        ]
                    },
                    "seller_name": {
                        "$concat": [
                            {"$ifNull": ["$seller.first_name", ""]},
                            " ",
                            {"$ifNull": ["$seller.last_name", ""]}
                        ]
                    },
                    "agent_name": {
                        "$concat": [
                            {"$ifNull": ["$agent.first_name", ""]},
                            " ",
                            {"$ifNull": ["$agent.last_name", ""]}
                        ]
                    }
                }
            },
            # Sort
            {
                "$sort": {
                    sort_by: 1 if sort_order == "asc" else -1
                }
            },
            # Count total documents for pagination
            {
                "$facet": {
                    "metadata": [
                        {"$count": "total"}
                    ],
                    "data": [
                        {"$skip": skip},
                        {"$limit": per_page}
                    ]
                }
            }
        ]
        
        # Execute aggregation
        result = await db.showing_requests.aggregate(pipeline).to_list(length=1)
        result = result[0] if result else {"metadata": [{"total": 0}], "data": []}
        
        # Get total count and requests
        total_docs = result["metadata"][0]["total"] if result["metadata"] else 0
        requests = result["data"]
        
        # Calculate pagination values
        total_pages = (total_docs + per_page - 1) // per_page
        start_page = max(1, page - 2)
        end_page = min(total_pages, page + 2)
        page_range = list(range(start_page, end_page + 1))
        start_showing = (page - 1) * per_page + 1
        end_showing = min(page * per_page, total_docs)
        
        # Get selected agent and property details if they exist
        selected_agent = None
        selected_property = None
        
        if agent_id and agent_id != "None":
            agent = await db.users.find_one({"uuid": agent_id})
            if agent:
                selected_agent = {
                    "id": agent["uuid"],
                    "name": f"{agent.get('first_name', '')} {agent.get('last_name', '')}".strip(),
                    "email": agent.get("email", "")
                }
        
        if property_id and property_id != "None":
            property = await db.properties.find_one({"_id": ObjectId(property_id)})
            if property:
                selected_property = {
                    "id": str(property["_id"]),
                    "address": property.get("address", "Unknown Address"),
                    "listing_id": property.get("listing_id")
                }

        context = {
            "request": request,
            "resources": resources,
            "resource_label": "Showing Requests",
            "page_pre_title": "Property Showings",
            "page_title": "Showing Requests",
            "requests": requests,
            "current_page": page,
            "total_pages": total_pages,
            "total_docs": total_docs,
            "per_page": per_page,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "prev_page": page - 1,
            "next_page": page + 1,
            "page_range": page_range,
            "start_showing": start_showing,
            "end_showing": end_showing,
            "search": None,
            "agent_id": agent_id if agent_id != "None" else None,
            "property_id": property_id if property_id != "None" else None,
            "status": status if status != "None" else None,
            "date_from": date_from,
            "date_to": date_to,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "selected_agent": selected_agent,
            "selected_property": selected_property
        }
        
        return templates.TemplateResponse(
            "showing-requests.html",
            context=context
        )
    except Exception as e:
        logger.error(f"Error in showing requests route: {str(e)}", exc_info=True)
        return templates.TemplateResponse(
            "showing-requests.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "Showing Requests",
                "page_pre_title": "Property Showings",
                "page_title": "Showing Requests",
                "error": f"Failed to load showing requests: {str(e)}",
                "requests": [],
                "current_page": 1,
                "total_pages": 1,
                "total_docs": 0,
                "per_page": per_page,
                "has_prev": False,
                "has_next": False,
                "prev_page": 1,
                "next_page": 1,
                "page_range": [1],
                "start_showing": 0,
                "end_showing": 0,
                "selected_agent": None,
                "selected_property": None
            },
        )

@app.post("/showing-requests/{request_id}/process", dependencies=[Depends(PermissionDependency(["manage_showing_requests"]))])
async def process_showing_request(
    request: Request,
    request_id: str,
    action: str = Form(...),
    notes: str = Form(None),
    reason: str = Form(None),
    alternate_time: str = Form(None),
    admin=Depends(get_current_admin),
):
    """Process a showing request with the selected action."""
    try:
        client = app.state.mongodb_client
        db = client.API
        # Get the showing request
        showing_request = await db.showing_requests.find_one({"_id": ObjectId(request_id)})
        if not showing_request:
            raise HTTPException(status_code=404, detail="Showing request not found")

        # Validate the request is in pending status
        if showing_request["status"] != "pending":
            raise HTTPException(status_code=400, detail="Can only process pending requests")

        # Prepare update based on action
        update_data = {
            "updated_at": datetime.utcnow(),
            "processing": {
                "processed_by": admin.username,
                "processed_at": datetime.utcnow(),
                "notes": notes
            }
        }

        if action == "approve":
            update_data["status"] = "approved"
            update_data["approved_datetime"] = datetime.utcnow()
            
        elif action == "deny":
            if not reason:
                raise HTTPException(status_code=400, detail="Reason is required for denial")
            update_data["status"] = "denied"
            update_data["processing"]["denial_reason"] = reason
            
        elif action == "reschedule":
            if not alternate_time:
                raise HTTPException(status_code=400, detail="Alternate time is required for rescheduling")
            update_data["status"] = "rescheduled"
            update_data["alternate_times"] = [{
                "proposed_time": datetime.fromisoformat(alternate_time),
                "proposed_by": admin.username,
                "proposed_at": datetime.utcnow(),
                "notes": notes
            }]
            
        else:
            raise HTTPException(status_code=400, detail="Invalid action")

        # Update the showing request
        await db.showing_requests.update_one(
            {"_id": ObjectId(request_id)},
            {"$set": update_data}
        )

        # Create audit log entry
        audit_entry = {
            "timestamp": datetime.now(),
            "admin_username": admin.username,
            "action": f"showing_request_{action}",
            "request_id": request_id,
            "details": {
                "previous_status": showing_request["status"],
                "new_status": update_data["status"],
                "notes": notes,
                "reason": reason if action == "deny" else None,
                "alternate_time": alternate_time if action == "reschedule" else None
            }
        }
        await db.audit_log.insert_one(audit_entry)

        # Send notification to the requester
        if showing_request.get("requester_id"):
            user = await db.users.find_one({"uuid": showing_request["requester_id"]})
            if user and user.get("device_token"):
                # Set notification text based on action
                if action == "approve":
                    title = "Showing Request Approved"
                    message = f"Your showing request has been approved for {showing_request.get('property_address', 'the property')}"
                elif action == "deny":
                    title = "Showing Request Denied"
                    message = f"Your showing request has been denied. Reason: {reason}"
                elif action == "reschedule":
                    title = "Showing Request Rescheduled"
                    message = f"Your showing request has been rescheduled to {alternate_time}"

                # Send FCM notification
                fcm_service = FCMService()
                asyncio.create_task(fcm_service.send_notification(
                    tokens=[user["device_token"]],
                    title=title,
                    body=message,
                    data={"request_id": request_id},
                    notification_id=str(showing_request["_id"]),
                    db_client=client
                ))

                # Create user notification
                await db.user_notifications.insert_one({
                    "user_id": showing_request["requester_id"],
                    "notification_id": showing_request["_id"],
                    "title": title,
                    "message": message,
                    "created_at": datetime.now(),
                    "is_read": False,
                    "type": "showing_request",
                    "status": "sent",
                    "metadata": {
                        "request_id": request_id,
                        "action": action,
                        "admin_username": admin.username
                    }
                })

        return RedirectResponse(url="/admin/showing-requests", status_code=303)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/showing-requests/export", dependencies=[Depends(PermissionDependency(["manage_showing_requests"]))])
async def export_showing_requests(
    request: Request,
    admin=Depends(get_current_admin),
    agent_id: str = Form(None),
    property_id: str = Form(None),
    status: str = Form(None),
    date_from: str = Form(None),
    date_to: str = Form(None),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        
        # Build match conditions
        match_conditions = {}
            
        if agent_id and agent_id != "None":
            match_conditions["agent_id"] = agent_id
            
        if property_id and property_id != "None":
            match_conditions["property_id"] = ObjectId(property_id)
            
        if status and status != "None":
            match_conditions["status"] = status
            
        if date_from:
            try:
                date_from_dt = datetime.fromisoformat(date_from)
                match_conditions["requested_datetime"] = {"$gte": date_from_dt}
            except ValueError:
                pass
                
        if date_to:
            try:
                date_to_dt = datetime.fromisoformat(date_to)
                if "requested_datetime" in match_conditions:
                    match_conditions["requested_datetime"]["$lte"] = date_to_dt
                else:
                    match_conditions["requested_datetime"] = {"$lte": date_to_dt}
            except ValueError:
                pass
        
        # Create aggregation pipeline
        pipeline = [
            # Match showing requests based on conditions
            {"$match": match_conditions},
            # Lookup property details
            {
                "$lookup": {
                    "from": "properties",
                    "let": { "property_id": "$property_id" },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": { "$eq": ["$_id", "$$property_id"] }
                            }
                        }
                    ],
                    "as": "property"
                }
            },
            # Lookup requester details
            {
                "$lookup": {
                    "from": "users",
                    "let": { "requester_id": "$requester_id" },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": { "$eq": ["$uuid", "$$requester_id"] }
                            }
                        }
                    ],
                    "as": "requester"
                }
            },
            # Lookup seller details
            {
                "$lookup": {
                    "from": "users",
                    "let": { "seller_id": "$seller_id" },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": { "$eq": ["$uuid", "$$seller_id"] }
                            }
                        }
                    ],
                    "as": "seller"
                }
            },
            # Lookup agent details if exists
            {
                "$lookup": {
                    "from": "users",
                    "let": { "agent_id": "$agent_id" },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": { "$eq": ["$uuid", "$$agent_id"] }
                            }
                        }
                    ],
                    "as": "agent"
                }
            },
            # Unwind arrays (we expect one document per lookup)
            {"$unwind": {"path": "$property", "preserveNullAndEmptyArrays": True}},
            {"$unwind": {"path": "$requester", "preserveNullAndEmptyArrays": True}},
            {"$unwind": {"path": "$seller", "preserveNullAndEmptyArrays": True}},
            {"$unwind": {"path": "$agent", "preserveNullAndEmptyArrays": True}},
            # Add computed fields
            {
                "$addFields": {
                    "property_address": "$property.address",
                    "requester_name": {
                        "$concat": [
                            {"$ifNull": ["$requester.first_name", ""]},
                            " ",
                            {"$ifNull": ["$requester.last_name", ""]}
                        ]
                    },
                    "seller_name": {
                        "$concat": [
                            {"$ifNull": ["$seller.first_name", ""]},
                            " ",
                            {"$ifNull": ["$seller.last_name", ""]}
                        ]
                    },
                    "agent_name": {
                        "$concat": [
                            {"$ifNull": ["$agent.first_name", ""]},
                            " ",
                            {"$ifNull": ["$agent.last_name", ""]}
                        ]
                    }
                }
            },
            # Sort by requested datetime
            {"$sort": {"requested_datetime": -1}}
        ]
        
        requests = await db.showing_requests.aggregate(pipeline).to_list(length=None)
        
        # Prepare data for export
        data = []
        for req in requests:
            data.append({
                'Property Address': req.get('property_address', 'N/A'),
                'Requester Name': req.get('requester_name', 'N/A').strip(),
                'Requester Email': req.get('requester', {}).get('email', 'N/A'),
                'Requester Phone': f"'{req.get('requester', {}).get('phone', 'N/A')}'",
                'Requested Time': req.get('requested_datetime').strftime('%Y-%m-%d %H:%M') if req.get('requested_datetime') else 'N/A',
                'Status': req.get('status', 'N/A').title(),
                'Agent Name': req.get('agent_name', 'N/A').strip(),
                'Number of Visitors': req.get('request_details', {}).get('num_visitors', 'N/A'),
                'Financing Status': req.get('request_details', {}).get('financing_status', 'N/A'),
                'Special Requests': req.get('request_details', {}).get('special_requests', 'N/A'),
                'Created At': req.get('created_at').strftime('%Y-%m-%d %H:%M') if req.get('created_at') else 'N/A',
                'Updated At': req.get('updated_at').strftime('%Y-%m-%d %H:%M') if req.get('updated_at') else 'N/A'
            })
        
        if not data:
            # Return empty CSV with headers if no data
            data.append({
                'Property Address': '',
                'Requester Name': '',
                'Requester Email': '',
                'Requester Phone': '',
                'Requested Time': '',
                'Status': '',
                'Agent Name': '',
                'Number of Visitors': '',
                'Financing Status': '',
                'Special Requests': '',
                'Created At': '',
                'Updated At': ''
            })
        
        # Export as CSV
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
        output.seek(0)
        
        return StreamingResponse(
            iter([output.getvalue().encode('utf-8')]),
            media_type='text/csv',
            headers={
                'Content-Disposition': f'attachment; filename=showing_requests_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            }
        )
        
    except Exception as e:
        logger.error(f"Error exporting showing requests: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to export showing requests")

@app.get("/agents/search", dependencies=[Depends(PermissionDependency(["search_agents"]))])
async def search_agents(
    request: Request,
    query: str = Query(..., min_length=1),
    admin=Depends(get_current_admin),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        users_collection = db.users
        
        # Search for agents with matching email or name (case-insensitive)
        regex_pattern = f".*{query}.*"
        query = {
            "$or": [
                {"email": {"$regex": regex_pattern, "$options": "i"}},
                {"first_name": {"$regex": regex_pattern, "$options": "i"}},
                {"last_name": {"$regex": regex_pattern, "$options": "i"}}
            ]
        }
        
        # Limit to 10 results for performance
        cursor = users_collection.find(query).limit(10)
        agents = []
        async for doc in cursor:
            agents.append({
                "id": str(doc["uuid"]),
                "name": f"{doc.get('first_name', '')} {doc.get('last_name', '')}".strip(),
                "email": doc.get("email", "")
            })
            
        return {"agents": agents}
        
    except Exception as e:
        logger.error(f"Error searching agents: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to search agents")

@app.get("/properties/search", dependencies=[Depends(PermissionDependency(["search_properties"]))])
async def search_properties(
    request: Request,
    query: str = Query(..., min_length=1),
    admin=Depends(get_current_admin),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        properties_collection = db.properties
        
        # Search for properties with matching address (case-insensitive)
        regex_pattern = f".*{query}.*"
        query = {
            "address": {"$regex": regex_pattern, "$options": "i"}
        }
        
        # Limit to 10 results for performance
        cursor = properties_collection.find(query).limit(10)
        properties = []
        async for doc in cursor:
            properties.append({
                "id": str(doc["_id"]),
                "address": doc.get("address", ""),
                "listing_id": doc.get("ListingId", "")
            })
            
        return {"properties": properties}
        
    except Exception as e:
        logger.error(f"Error searching properties: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to search properties")
