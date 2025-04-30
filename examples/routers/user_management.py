from fastapi import Depends, HTTPException, Query, Form
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER, HTTP_404_NOT_FOUND
import logging
from datetime import datetime

from examples.services.fcm_service import FCMService
from fastapi_admin.app import app
from fastapi_admin.depends import get_resources, get_current_admin
from fastapi_admin.template import templates
from examples.permissions import PermissionDependency


# Configure logging
logger = logging.getLogger(__name__)

@app.get("/user-management", dependencies=[Depends(PermissionDependency(["view_user_management"]))])
async def user_management(
    request: Request,
    resources=Depends(get_resources),
    admin=Depends(get_current_admin),
    page: int = Query(1, ge=1),
    per_page: int = 10,
    search: str = Query(None),
    user_type: str = Query(None, regex="^(buyer|seller|agent|admin|None)$"),
    verification_status: str = Query(None, regex="^(verified|rejected|pending|None)$"),
    sort_by: str = Query("created_at", regex="^(created_at|last_active|name)$"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        
        # Calculate skip and limit for pagination
        skip = (page - 1) * per_page
        
        # Build match conditions
        match_conditions = {}
        
        if search:
            match_conditions["$or"] = [
                {"first_name": {"$regex": search, "$options": "i"}},
                {"last_name": {"$regex": search, "$options": "i"}},
                {"email": {"$regex": search, "$options": "i"}},
                {"phone": {"$regex": search, "$options": "i"}}
            ]
            
        if verification_status and verification_status != "None":
            if verification_status == "verified":
                match_conditions["is_id_verified"] = True
            elif verification_status == "rejected":
                match_conditions["$and"] = [
                    {"is_id_verified": False},
                    {"verification_status": "rejected"}
                ]
            elif verification_status == "pending":
                match_conditions["$or"] = [
                    {"is_id_verified": None},
                ]
        
        # Create aggregation pipeline
        pipeline = [
            # Match users based on conditions
            {"$match": match_conditions},
            # Lookup property transactions
            {
                "$lookup": {
                    "from": "property_seller_transaction",
                    "let": { "user_uuid": "$uuid" },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": { "$eq": ["$seller_id", "$$user_uuid"] }
                            }
                        }
                    ],
                    "as": "property_transactions"
                }
            },
            # Add computed fields
            {
                "$addFields": {
                    "full_name": {
                        "$concat": [
                            {"$ifNull": ["$first_name", ""]},
                            " ",
                            {"$ifNull": ["$last_name", ""]}
                        ]
                    },
                    "property_count": {"$size": "$property_transactions"},
                    "last_active": {
                        "$ifNull": [
                            "$last_active",
                            "$created_at"
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
        result = await db.users.aggregate(pipeline).to_list(length=1)
        result = result[0] if result else {"metadata": [{"total": 0}], "data": []}
        
        # Get total count and users
        total_docs = result["metadata"][0]["total"] if result["metadata"] else 0
        users = result["data"]
        
        # Calculate pagination values
        total_pages = (total_docs + per_page - 1) // per_page
        start_page = max(1, page - 2)
        end_page = min(total_pages, page + 2)
        page_range = list(range(start_page, end_page + 1))
        start_showing = (page - 1) * per_page + 1
        end_showing = min(page * per_page, total_docs)
        
        context = {
            "request": request,
            "resources": resources,
            "resource_label": "User Management",
            "page_pre_title": "User Directory",
            "page_title": "User Management",
            "users": users,
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
            "search": search,
            "user_type": user_type if user_type != "None" else None,
            "verification_status": verification_status if verification_status != "None" else None,
            "sort_by": sort_by,
            "sort_order": sort_order
        }
        
        return templates.TemplateResponse(
            "user-management.html",
            context=context
        )
    except Exception as e:
        logger.error(f"Error in user management route: {str(e)}", exc_info=True)
        return templates.TemplateResponse(
            "user-management.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "User Management",
                "page_pre_title": "User Directory",
                "page_title": "User Management",
                "error": f"Failed to load users: {str(e)}",
                "users": [],
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
                "search": search,
                "user_type": user_type if user_type != "None" else None,
                "verification_status": verification_status if verification_status != "None" else None,
                "sort_by": sort_by,
                "sort_order": sort_order
            },
        )

@app.get("/user-management/{user_uuid}", dependencies=[Depends(PermissionDependency(["view_user_management"]))])
async def view_user_profile(
    request: Request,
    user_uuid: str,
    resources=Depends(get_resources),
    admin=Depends(get_current_admin),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        
        # Get user details
        user = await db.users.find_one({"uuid": user_uuid})
        if not user:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="User not found")
            
        # Get user's properties through property_seller_transaction
        pipeline = [
            # Match transactions for this seller
            {
                "$match": {
                    "seller_id": user_uuid
                }
            },
            # Lookup the property details
            {
                "$lookup": {
                    "from": "properties",
                    "let": { "property_id": { "$toObjectId": "$property_id" } },
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
            # Unwind the property array (we expect one property per transaction)
            {
                "$unwind": "$property"
            },
            # Project only the fields we need
            {
                "$project": {
                    "_id": "$property._id",
                    "status": "$property.status",
                    "address": "$property.address",
                    "created_at": "$property.created_at",
                    "updated_at": "$property.updated_at",
                    "listing_id": "$property.ListingId"
                }
            }
        ]
        
        # Execute the aggregation pipeline
        properties_cursor = db.property_seller_transaction.aggregate(pipeline)
        properties = await properties_cursor.to_list(length=None)
        
        # Get user's documents
        documents = await db.users_uploaded_docs.find_one({"uuid": user_uuid})
        if documents:
            documents = documents.get("uploaded_documents", [])
        else:
            documents = []
            
        # Format user data
        user_data = {
            "uuid": user.get("uuid"),
            "first_name": user.get("first_name", ""),
            "last_name": user.get("last_name", ""),
            "email": user.get("email", ""),
            "phone": user.get("phone", ""),
            "company": user.get("company", ""),
            "is_id_verified": user.get("is_id_verified", False),
            "verification_status": user.get("verification_status", "pending"),
            "rejection_reason": user.get("rejection_reason", ""),
            "created_at": user.get("created_at"),
            "last_active": user.get("last_active"),
            "profile_pic": user.get("profile_pic", "")
        }
        
        # Format properties data
        properties_data = []
        for prop in properties:
            properties_data.append({
                "id": str(prop["_id"]),
                "title": prop.get("title", "Untitled Property"),
                "status": prop.get("status", "draft"),
                "address": prop.get("address", ""),
                "created_at": prop.get("created_at"),
                "updated_at": prop.get("updated_at")
            })
            
        # Format documents data
        documents_data = []
        for doc in documents:
            documents_data.append({
                "id": str(doc.get("_id", "")),
                "name": doc.get("name", "Unnamed Document"),
                "type": doc.get("type", "unknown"),
                "uploaded_at": doc.get("uploaded_at"),
                "url": doc.get("url", "")
            })
            
        context = {
            "request": request,
            "resources": resources,
            "resource_label": "User Profile",
            "page_pre_title": "User Management",
            "page_title": f"User Profile - {user_data['first_name']} {user_data['last_name']}",
            "user": user_data,
            "properties": properties_data,
            "documents": documents_data
        }
        
        return templates.TemplateResponse(
            "user-profile.html",
            context=context
        )
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error fetching user profile: {str(e)}")
        return templates.TemplateResponse(
            "user-profile.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "User Profile",
                "page_pre_title": "User Management",
                "page_title": "User Profile",
                "error": f"Failed to load user profile: {str(e)}"
            }
        )

@app.post("/user-management/{user_uuid}/update-info", dependencies=[Depends(PermissionDependency(["manage_user_information"]))])
async def update_user_info(
    request: Request,
    user_uuid: str,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(None),
    company: str = Form(None),
    admin=Depends(get_current_admin),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        
        # Update user information
        update_data = {
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "phone": phone,
            "company": company,
            "updated_at": datetime.now()
        }
        
        # Remove None values
        update_data = {k: v for k, v in update_data.items() if v is not None}
        
        result = await db.users.update_one(
            {"uuid": user_uuid},
            {"$set": update_data}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="User not found or no changes made")
            
        # Create audit log entry
        audit_entry = {
            "timestamp": datetime.now(),
            "admin_username": admin.username,
            "action": "user_info_update",
            "user_id": user_uuid,
            "details": update_data
        }
        
        # Save audit log
        await db.audit_log.insert_one(audit_entry)
        
        return RedirectResponse(
            url=f"/admin/user-management/{user_uuid}",
            status_code=HTTP_303_SEE_OTHER
        )
        
    except Exception as e:
        logger.error(f"Error updating user info: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update user information")

@app.post("/user-management/{user_uuid}/verify", dependencies=[Depends(PermissionDependency(["manage_user_verification"]))])
async def update_verification(
    request: Request,
    user_uuid: str,
    action: str = Form(...),
    verified_as: str = Form(None),
    verified_name: str = Form(None),
    rejection_reason: str = Form(None),
    admin=Depends(get_current_admin),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        
        if action == "verify":
            if not verified_as or not verified_name:
                raise HTTPException(status_code=400, detail="Verified as and verified name are required for verification")
                
            update_data = {
                "is_id_verified": True,
                "verification_status": "verified",
                "verified_at": datetime.now(),
                "verified_as": verified_as,
                "verified_name": verified_name,
                "rejection_reason": None,
                "updated_at": datetime.now()
            }
            
            notification_title = "ID Verification Approved"
            notification_body = f"Your ID verification has been approved as {verified_as} - {verified_name}"
            
        elif action == "reject":
            if not rejection_reason:
                raise HTTPException(status_code=400, detail="Rejection reason is required")
                
            update_data = {
                "is_id_verified": False,
                "verification_status": "rejected",
                "verified_at": None,
                "verified_as": None,
                "verified_name": None,
                "rejection_reason": rejection_reason,
                "updated_at": datetime.now()
            }
            
            notification_title = "ID Verification Rejected"
            notification_body = f"Your ID verification has been rejected. Reason: {rejection_reason}"
            
        else:
            raise HTTPException(status_code=400, detail="Invalid action")
        
        result = await db.users.update_one(
            {"uuid": user_uuid},
            {"$set": update_data}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="User not found")
            
        # Create audit log entry
        audit_entry = {
            "timestamp": datetime.now(),
            "admin_username": admin.username,
            "action": f"user_verification_{action}",
            "user_id": user_uuid,
            "details": update_data
        }
        
        # Save audit log
        await db.audit_log.insert_one(audit_entry)
        
        # Send notification to user
        user = await db.users.find_one({"uuid": user_uuid})
        if user and user.get("device_token"):
            fcm_service = FCMService()
            await fcm_service.send_notification(
                tokens=[user["device_token"]],
                title=notification_title,
                body=notification_body
            )
        
        return RedirectResponse(
            url=f"/admin/user-management/{user_uuid}",
            status_code=HTTP_303_SEE_OTHER
        )
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error updating verification: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update verification status")

