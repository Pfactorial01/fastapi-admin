from fastapi import Depends, File, HTTPException, Query, Form, UploadFile
import httpx
from starlette.requests import Request
from starlette.responses import RedirectResponse, StreamingResponse, JSONResponse
from starlette.status import HTTP_303_SEE_OTHER, HTTP_404_NOT_FOUND, HTTP_400_BAD_REQUEST, HTTP_500_INTERNAL_SERVER_ERROR, HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
import logging
from examples import settings
from bson import ObjectId
import markdown
from markdown.extensions import fenced_code, tables, nl2br
from datetime import datetime, timedelta
from typing import List, Optional
import os
import asyncio
import csv
import io
import pandas as pd
from urllib.parse import urlparse, parse_qs
import json
import yaml
import ast
from io import StringIO

from examples.models import Admin, Config, Groups
from examples.services.fcm_service import FCMService
from examples.triggers.executor import execute_trigger_action
from fastapi_admin.app import app
from fastapi_admin.depends import get_resources, get_current_admin
from fastapi_admin.template import templates
from examples.permissions import Permissions


# Configure logging
logger = logging.getLogger(__name__)

@app.get("/id-verification")
async def id_verification(
    request: Request,
    resources=Depends(get_resources),
    admin=Depends(get_current_admin),
    page: int = Query(1, ge=1),
    per_page: int = 10,
    status: str = Query("pending", regex="^(all|pending|approved|rejected)$"),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        
        # Calculate skip and limit for pagination
        skip = (page - 1) * per_page
        
        # Define match condition based on status
        if status == "all":
            match_condition = {}  # No conditions for 'all' status
        elif status == "approved":
            match_condition = {
                "user.is_id_verified": True
            }
        elif status == "rejected":
            match_condition = {
                "user.verification_status": "rejected"
            }
        else:  # pending
            match_condition = {
                "$and": [
                    {"$or": [{"user.is_id_verified": False}, {"user.is_id_verified": None}]},
                    {
                        "$or": [
                            {"user.verification_status": {"$exists": False}},
                            {"user.verification_status": "pending"}
                        ]
                    }
                ]
            }
        
        # Create aggregation pipeline
        pipeline = [
            # Join with users collection first
            {
                "$lookup": {
                    "from": "users",
                    "localField": "user_id",
                    "foreignField": "uuid",
                    "as": "user"
                }
            },
            # Unwind the user array (we expect one user per verification)
            {
                "$unwind": "$user"
            },
            # Match based on status
            {
                "$match": match_condition
            },
            # Sort by verification date (newest first)
            {
                "$sort": {
                    "verification_date": -1
                }
            },
            # Project only the fields we need
            {
                "$project": {
                    "_id": 1,
                    "user_id": 1,
                    "user_name": {
                        "$concat": [
                            {"$ifNull": ["$user.first_name", ""]},
                            " ",
                            {"$ifNull": ["$user.last_name", ""]}
                        ]
                    },
                    "user_email": "$user.email",
                    "user_phone": "$user.phone",
                    "profile_pic": "$user.profile_pic",
                    "is_id_verified": "$user.is_id_verified",
                    "verification_status": {
                        "$ifNull": ["$user.verification_status", "pending"]
                    },
                    "rejection_reason": "$user.rejection_reason",
                    "latest_documents": {"$arrayElemAt": ["$documents", -1]},
                    "verification_date": 1
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
        result = await db.ID_verifications.aggregate(pipeline).to_list(length=1)
        result = result[0] if result else {"metadata": [{"total": 0}], "data": []}
        
        # Get total count and verifications
        total_docs = result["metadata"][0]["total"] if result["metadata"] else 0
        verifications = result["data"]
        
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
            "resource_label": "ID Verification",
            "page_pre_title": "User Verification",
            "page_title": "ID Verification",
            "verifications": verifications,
            "current_status": status,
            "pagination": {
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
            }
        }
        
        return templates.TemplateResponse(
            "id-verification.html",
            context=context
        )
    except Exception as e:
        logger.error(f"Error in ID verification route: {str(e)}", exc_info=True)
        return templates.TemplateResponse(
            "id-verification.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "ID Verification",
                "page_pre_title": "User Verification",
                "page_title": "ID Verification",
                "error": f"Failed to load verifications: {str(e)}",
                "verifications": [],
                "pagination": {
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
                }
            },
        )

@app.post("/id-verification/update", dependencies=[Depends(Permissions.MANAGE_ID_VERIFICATIONS)])
async def update_verification(
    request: Request,
    user_id: str = Form(...),
    status: str = Form(...),
    rejection_reason: str = Form(None),
    verified_as: str = Form(None),
    verified_name: str = Form(None),
    admin=Depends(get_current_admin),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        users_collection = db.users
        audit_collection = db.verification_audit_log
        
        # Update user verification status
        update_data = {
            "is_id_verified": status == "approved",
            "verification_status": status,
            "verified_at": datetime.now() if status == "approved" else None
        }
        
        if status == "rejected" and rejection_reason:
            update_data["rejection_reason"] = rejection_reason
        elif status == "approved" and verified_as and verified_name:
            update_data["verified_as"] = verified_as
            update_data["verified_name"] = verified_name
        
        # Get user data before update for audit log
        user_before = await users_collection.find_one({"uuid": user_id})
        
        # Perform the update
        result = await users_collection.update_one(
            {"uuid": user_id},
            {"$set": update_data}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="User not found")
            
        # Create audit log entry
        audit_entry = {
            "timestamp": datetime.now(),
            "admin_username": admin.username,
            "action": "verification_update",
            "user_id": user_id,
            "user_email": user_before.get("email") if user_before else None,
            "status_from": user_before.get("verification_status", "pending") if user_before else None,
            "status_to": status,
            "details": {
                "is_id_verified": status == "approved",
                "verified_at": update_data.get("verified_at"),
                "verified_as": verified_as if status == "approved" else None,
                "verified_name": verified_name if status == "approved" else None,
                "rejection_reason": rejection_reason if status == "rejected" else None
            }
        }
        
        # Save audit log
        await audit_collection.insert_one(audit_entry)
        
        # Send notifications
        user = await users_collection.find_one({"uuid": user_id})
        if user:
            # Send FCM notification
            if user.get("device_token"):
                fcm_service = FCMService()
                await fcm_service.send_notification(
                    tokens=[user["device_token"]],
                    title="ID Verification Update",
                    body=f"Your ID verification has been {status}" + 
                         (f" - {rejection_reason}" if status == "rejected" and rejection_reason else "")
                )
        
        return RedirectResponse(url="/admin/id-verification", status_code=HTTP_303_SEE_OTHER)
        
    except Exception as e:
        logger.error(f"Error updating verification: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update verification status")
