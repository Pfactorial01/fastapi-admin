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

@app.get("/notifications", dependencies=[Depends(Permissions.VIEW_NOTIFICATIONS)])
async def notifications(
    request: Request,
    resources=Depends(get_resources),
    admin=Depends(get_current_admin),
    page: int = Query(1, ge=1),
    per_page: int = 10,
):
    try:            
        client = app.state.mongodb_client        
        # Get the database and collection
        db = client.API
        notifications_collection = db.notifications
        users_collection = db.users
        
        # Calculate skip and limit for pagination
        skip = (page - 1) * per_page
        
        # Get total count for pagination
        total_docs = await notifications_collection.count_documents({})
        total_pages = (total_docs + per_page - 1) // per_page
        
        # Calculate pagination range
        start_page = max(1, page - 2)
        end_page = min(total_pages, page + 2)
        page_range = list(range(start_page, end_page + 1))
        
        # Calculate showing range
        start_showing = (page - 1) * per_page + 1
        end_showing = min(page * per_page, total_docs)
        
        # Fetch notifications with pagination
        cursor = notifications_collection.find({}).sort("created_at", -1).skip(skip).limit(per_page)
        notifications = []
        async for doc in cursor:
            notifications.append({
                "_id": str(doc["_id"]),
                "title": doc.get("title", ""),
                "message": doc.get("message", ""),
                "target_type": doc.get("target_type", "all"),  # all, individual, radius
                "target_users": doc.get("target_users", []),
                "target_radius": doc.get("target_radius", None),
                "target_location": doc.get("target_location", None),
                "scheduled_for": doc.get("scheduled_for", None),
                "created_at": doc.get("created_at", datetime.now()),
                "status": doc.get("status", "pending"),  # pending, sent, failed
            })
        

        return templates.TemplateResponse(
            "notifications.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "Notifications",
                "page_pre_title": "System Notifications",
                "page_title": "Notifications",
                "notifications": notifications,
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
            },
        )
    except Exception as e:
        logger.error(f"Error fetching notifications: {str(e)}")
        return templates.TemplateResponse(
            "notifications.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "Notifications",
                "page_pre_title": "System Notifications",
                "page_title": "Notifications",
                "error": "Failed to fetch notifications",
                "notifications": [],
                "users": [],
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

@app.post("/notifications/send", dependencies=[Depends(Permissions.MANAGE_NOTIFICATIONS)])
async def send_notification(
    request: Request,
    title: str = Form(...),
    message: str = Form(...),
    target_type: str = Form(...),  # all, individual, radius
    target_users: List[str] = Form([]),
    target_radius: float = Form(None),
    target_lat: float = Form(None),
    target_lng: float = Form(None),
    schedule_type: str = Form(...),  # immediate or scheduled
    scheduled_for: str = Form(None),  # Optional now
    link: str = Form(None),  # Add link parameter
    admin=Depends(get_current_admin),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        notifications_collection = db.notifications
        
        # Create notification document
        notification = {
            "title": title,
            "message": message,
            "target_type": target_type,
            "created_at": datetime.now(),
            "status": "pending",
            "link": link if link else None,  # Add link to notification document
        }

        # Handle scheduling
        if schedule_type == "scheduled":
            if not scheduled_for:
                raise HTTPException(
                    status_code=400,
                    detail="Scheduled time is required when scheduling for later"
                )
            
            # Validate scheduled_for is a future datetime
            scheduled_datetime = datetime.fromisoformat(scheduled_for.replace('Z', '+00:00'))
            now = datetime.now()
            if scheduled_datetime <= now:
                raise HTTPException(
                    status_code=400,
                    detail="Scheduled time must be in the future"
                )
            notification["scheduled_for"] = scheduled_datetime
        else:  # immediate
            notification["scheduled_for"] = datetime.now()
            notification["status"] = "processing"  # Start processing immediately

        # Add target-specific fields
        if target_type == "individual" and target_users:
            notification["target_users"] = target_users
        elif target_type == "radius" and target_radius and target_lat and target_lng:
            notification["target_radius"] = float(target_radius)
            notification["target_location"] = {
                "type": "Point",
                "coordinates": [float(target_lng), float(target_lat)]
            }
            
        # Insert notification
        result = await notifications_collection.insert_one(notification)
        notification_id = str(result.inserted_id)

        # If immediate, process the notification right away
        if schedule_type == "immediate":
            try:
                # Get target users
                target_users = []
                if notification["target_type"] == "all":
                    async for user in db.users.find({"device_token": {"$exists": True}}):
                        target_users.append(user["uuid"])
                elif notification["target_type"] == "individual":
                    target_users = notification["target_users"]
                elif notification["target_type"] == "radius":
                    location = notification["target_location"]["coordinates"]
                    radius_meters = notification["target_radius"] * 1000
                    async for user in db.users.find({
                        "location": {
                            "$nearSphere": {
                                "$geometry": {
                                    "type": "Point",
                                    "coordinates": location
                                },
                                "$maxDistance": radius_meters
                            }
                        }
                    }):
                        target_users.append(user["uuid"])

                # Get device tokens
                tokens = []
                async for user in db.users.find({"uuid": {"$in": target_users}}):
                    if user.get("device_token"):
                        tokens.append(user["device_token"])

                # Send notification
                fcm_service = FCMService()
                # Create task instead of awaiting
                asyncio.create_task(fcm_service.send_notification(
                    tokens=tokens,
                    title=notification["title"],
                    body=notification["message"],
                    data={"link": notification["link"]} if notification.get("link") else None,
                    notification_id=str(result.inserted_id),
                    db_client=client
                ))

                # Create user notifications in bulk
                user_notifications = [
                    {
                        "user_id": user_id,
                        "notification_id": result.inserted_id,
                        "title": notification["title"],
                        "message": notification["message"],
                        "link": notification.get("link"),
                        "created_at": datetime.now(),
                        "is_read": False,
                        "type": "system",  # Indicates this is a system notification
                        "status": "sent",
                        "metadata": {
                            "target_type": notification["target_type"],
                            "admin_id": admin.id,
                            "admin_username": admin.username
                        }
                    }
                    for user_id in target_users
                ]

                if user_notifications:
                    # Use ordered=False for better performance
                    await db.user_notifications.insert_many(user_notifications, ordered=False)

                # Update notification status to processing
                await notifications_collection.update_one(
                    {"_id": result.inserted_id},
                    {
                        "$set": {
                            "status": "processing",
                            "processed_at": datetime.now()
                        }
                    }
                )
            except Exception as e:
                logger.error(f"Error processing immediate notification: {str(e)}")
                await notifications_collection.update_one(
                    {"_id": result.inserted_id},
                    {
                        "$set": {
                            "status": "failed",
                            "error": str(e),
                            "processed_at": datetime.now()
                        }
                    }
                )
        
        return RedirectResponse(url="/admin/notifications", status_code=HTTP_303_SEE_OTHER)
        
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid datetime format for scheduled_for"
        )
    except Exception as e:
        logger.error(f"Error creating notification: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create notification")

@app.get("/users/search")
async def search_users(
    request: Request,
    email: str = Query(..., min_length=1),
    admin=Depends(get_current_admin),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        users_collection = db.users
        
        # Search for users with matching email (case-insensitive)
        regex_pattern = f".*{email}.*"
        query = {
            "email": {
                "$regex": regex_pattern,
                "$options": "i"
            }
        }
        
        # Limit to 10 results for performance
        cursor = users_collection.find(query).limit(10)
        users = []
        async for doc in cursor:
            users.append({
                "id": str(doc["uuid"]),
                "name": f"{doc.get('first_name', '')} {doc.get('last_name', '')}".strip(),
                "email": doc.get("email", "")
            })
            
        return {"users": users}
        
    except Exception as e:
        logger.error(f"Error searching users: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to search users")

