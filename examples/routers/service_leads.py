from fastapi import Depends, HTTPException, Query, Form
from starlette.requests import Request
from starlette.responses import StreamingResponse
from bson import ObjectId
from datetime import datetime, timedelta
from typing import Optional
import csv
from io import StringIO

from examples.models import Admin
from fastapi_admin.app import app
from fastapi_admin.depends import get_resources, get_current_admin
from fastapi_admin.template import templates
from examples.permissions import PermissionDependency


@app.get("/service-leads", dependencies=[Depends(PermissionDependency(["view_service_leads"]))])
async def service_leads(
    request: Request,
    resources=Depends(get_resources),
    admin=Depends(get_current_admin),
    page: int = Query(1, ge=1),
    per_page: int = 10,
    service_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    user_email: Optional[str] = Query(None),
    sort_by: str = Query("created_at", regex="^(created_at|service_type|status)$"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
):
    try:
        mongodb_client = request.app.state.mongodb_client
        services_collection = mongodb_client.API.services   
        # Build query filters
        query = {}

        # Handle service type filter
        if service_type and service_type.lower() != 'none':
            if service_type in ['Financing', 'TitleServices', 'HomeInspection', 'InsuranceQuote']:
                query["Service"] = service_type

        # Handle status filter
        if status and status.lower() != 'none':
            if status in ['pending', 'contacted', 'in_progress', 'converted', 'closed']:
                query["status"] = status

        # Handle user email/name search
        if user_email and user_email.lower() != 'none':
            # Search in all possible email and name fields across different service types
            query["$or"] = [
                {"data.stepOne.email": {"$regex": user_email, "$options": "i"}},
                {"data.generalInfo.email": {"$regex": user_email, "$options": "i"}},
                {"data.stepOne.firstName": {"$regex": user_email, "$options": "i"}},
                {"data.stepOne.lastName": {"$regex": user_email, "$options": "i"}},
                {"data.generalInfo.firstName": {"$regex": user_email, "$options": "i"}},
                {"data.generalInfo.lastName": {"$regex": user_email, "$options": "i"}}
            ]

        # Handle date filters
        if (date_from and date_from.lower() != 'none') or (date_to and date_to.lower() != 'none'):
            date_query = {}
            if date_from and date_from.lower() != 'none':
                try:
                    date_query["$gte"] = datetime.strptime(date_from, "%Y-%m-%d")
                except ValueError:
                    pass

            if date_to and date_to.lower() != 'none':
                try:
                    date_query["$lte"] = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
                except ValueError:
                    pass

            if date_query:
                query["created_at"] = date_query

        # Calculate pagination
        skip = (page - 1) * per_page

        # Determine sort direction
        sort_direction = -1 if sort_order == "desc" else 1
        sort_field = sort_by if sort_by != "service_type" else "Service"

        # Get leads with pagination
        leads_cursor = services_collection.find(query)
        total_leads = await services_collection.count_documents(query)
        
        # Apply sorting and pagination
        leads_cursor = leads_cursor.sort(sort_field, sort_direction).skip(skip).limit(per_page)

        admins = await Admin.all()
        admin_list = [{"id": admin.id, "name": admin.username} for admin in admins]
        
        # Convert MongoDB documents to Python dictionaries and handle ObjectId serialization
        leads = []
        async for lead in leads_cursor:
            # Convert ObjectId to string
            lead['_id'] = str(lead['_id'])
            
            # Convert datetime objects to ISO format strings
            if 'created_at' in lead:
                lead['created_at'] = lead['created_at'].isoformat()
            if 'updated_at' in lead:
                lead['updated_at'] = lead['updated_at'].isoformat()
                
            # Handle nested datetime objects in notes
            if 'notes' in lead and lead['notes']:
                for note in lead['notes']:
                    if 'created_at' in note:
                        note['created_at'] = note['created_at'].isoformat()
            
            # Set default status to pending if not present
            if 'status' not in lead:
                lead['status'] = 'pending'

            if lead.get('assigned_to') != None:
                for admin in admin_list:
                    if admin['id'] == lead['assigned_to']:
                        lead['assigned_to'] = admin['name']
            
            leads.append(lead)

        # Calculate pagination info
        total_pages = (total_leads + per_page - 1) // per_page
        has_next = page < total_pages
        has_prev = page > 1

        # Return template with data
        return templates.TemplateResponse(
            "service-leads.html",
            {
                "request": request,
                "resources": resources,
                "page_title": "Service Leads",
                "admin": admin,
                "leads": leads,
                "total_leads": total_leads,
                "page": page,
                "has_next": has_next,
                "has_prev": has_prev,
                "service_type": service_type if service_type and service_type.lower() != 'none' else '',
                "status": status if status and status.lower() != 'none' else '',
                "date_from": date_from if date_from and date_from.lower() != 'none' else '',
                "date_to": date_to if date_to and date_to.lower() != 'none' else '',
                "user_email": user_email if user_email and user_email.lower() != 'none' else '',
            },
        )
    except Exception as e:
        print(f"Error in service_leads: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/service-leads/{lead_id}", dependencies=[Depends(PermissionDependency(["view_service_leads"]))])
async def get_lead_details(
    request: Request,
    lead_id: str,
):
    """Get detailed information about a specific lead"""
    try:
        # Get lead details
        lead = await request.app.state.mongodb_client.API.services.find_one({"_id": ObjectId(lead_id)})
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")

        # Convert ObjectId to string
        lead['_id'] = str(lead['_id'])
        
        # Convert datetime objects to ISO format strings
        if 'created_at' in lead:
            lead['created_at'] = lead['created_at'].isoformat()
        if 'updated_at' in lead:
            lead['updated_at'] = lead['updated_at'].isoformat()
        
        # Handle nested datetime objects in notes
        if 'notes' in lead and lead['notes']:
            for note in lead['notes']:
                if 'created_at' in note:
                    note['created_at'] = note['created_at'].isoformat()

        # Fetch available admins
        admins = await Admin.all()
        admin_list = [{"id": admin.id, "name": admin.username} for admin in admins]

        # Add admins to the response
        lead['available_admins'] = admin_list

        return lead

    except Exception as e:
        print(f"Error in get_lead_details: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/service-leads/{lead_id}/update", dependencies=[Depends(PermissionDependency(["manage_service_leads"]))])
async def update_lead_status(
    request: Request,
    lead_id: str,
    status: str = Form(..., regex="^(pending|contacted|in_progress|converted|closed)$"),
    notes: Optional[str] = Form(None),
    assigned_to: Optional[int] = Form(None),
    admin=Depends(get_current_admin),
):
    """Update the status and details of a service lead"""
    try:
        update_data = {
            "status": status,
            "updated_at": datetime.utcnow(),
            "updated_by": admin.id
        }

        if assigned_to:
            update_data["assigned_to"] = assigned_to

        operations = {"$set": update_data}

        if notes:
            # Add new note to the notes array
            note_entry = {
                "content": notes,
                "created_at": datetime.utcnow(),
                "created_by": admin.id
            }
            operations["$push"] = {"notes": note_entry}

        result = await request.app.state.mongodb_client.API.services.update_one(
            {"_id": ObjectId(lead_id)},
            operations
        )

        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Lead not found")

        return {"success": True}

    except Exception as e:
        print(f"Error in update_lead_status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/service-leads/export", dependencies=[Depends(PermissionDependency(["manage_service_leads"]))])
async def export_leads(
    request: Request,
    service_type: Optional[str] = Form(None),
    status: Optional[str] = Form(None),
    date_from: Optional[str] = Form(None),
    date_to: Optional[str] = Form(None),
    admin=Depends(get_current_admin),
):
    """Export filtered leads to CSV"""
    try:
        # Build query filters
        query = {}
        if service_type:
            query["Service"] = service_type
        if status:
            query["status"] = status
        if date_from or date_to:
            date_query = {}
            if date_from:
                date_query["$gte"] = datetime.strptime(date_from, "%Y-%m-%d")
            if date_to:
                date_query["$lte"] = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            if date_query:
                query["created_at"] = date_query

        # Get all matching leads
        leads = await request.app.state.mongodb_client.API.services.find(query).to_list(length=None)

        # Prepare CSV data
        output = StringIO()
        writer = csv.writer(output)
        
        # Write headers
        headers = [
            "ID", "Service Type", "Status", "Created At", "Updated At",
            "First Name", "Last Name", "Email", "Phone",
            "Address", "City", "State", "ZIP",
            "Assigned To", "Notes"
        ]
        writer.writerow(headers)

        # Write data rows
        for lead in leads:
            # Extract user info from either stepOne or generalInfo
            user_info = {}
            if 'data' in lead:
                if 'stepOne' in lead['data']:
                    user_info = lead['data']['stepOne']
                elif 'generalInfo' in lead['data']:
                    user_info = lead['data']['generalInfo']

            # Format notes
            notes = "; ".join([note['content'] for note in lead.get('notes', [])]) if 'notes' in lead else ""

            # Write row
            row = [
                str(lead['_id']),
                lead.get('Service', ''),
                lead.get('status', ''),
                lead['created_at'].strftime('%Y-%m-%d %H:%M:%S') if 'created_at' in lead else '',
                lead['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if 'updated_at' in lead else '',
                user_info.get('firstName', ''),
                user_info.get('lastName', ''),
                user_info.get('email', ''),
                user_info.get('phone', ''),
                user_info.get('address', ''),
                user_info.get('city', ''),
                user_info.get('state', ''),
                user_info.get('zip', ''),
                lead.get('assigned_to', ''),
                notes
            ]
            writer.writerow(row)

        # Prepare the response
        output.seek(0)
        headers = {
            'Content-Disposition': f'attachment; filename=service_leads_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        }
        return StreamingResponse(
            iter([output.getvalue()]), 
            media_type='text/csv', 
            headers=headers
        )

    except Exception as e:
        print(f"Error in export_leads: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
