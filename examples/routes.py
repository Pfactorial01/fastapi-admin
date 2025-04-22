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
from .permissions import Permissions

# Configure logging
logger = logging.getLogger(__name__)

# Initialize markdown converter with extensions
md = markdown.Markdown(extensions=['fenced_code', 'tables', 'nl2br'])

# Define event types for triggers
EVENT_TYPES = [
    {"id": "user_created", "name": "New User Registered"},
    {"id": "property_created", "name": "New Property Listed"},
    {"id": "payment_made", "name": "New Payment Made"},
    {"id": "showing_request_created", "name": "New Showing Requested"},
    {"id": "service_requested", "name": "New Service Request"}
]

# Define action types for triggers
ACTION_TYPES = [
    {"id": "send_email", "name": "Send Email"},
    {"id": "api_call", "name": "Send API Call"},
    {"id": "python_script", "name": "Execute Python Script"}
]

def has_markdown_syntax(text: str) -> bool:
    """Check if text contains any markdown syntax."""
    # Common markdown patterns to check for
    markdown_patterns = [
        r'#+\s',  # Headers
        r'\*\*|\*|__|_',  # Bold/Italic
        r'`[^`]+`',  # Inline code
        r'```[\s\S]+?```',  # Code blocks
        r'\[.*?\]\(.*?\)',  # Links
        r'!\[.*?\]\(.*?\)',  # Images
        r'>\s',  # Blockquotes
        r'[-*+]\s',  # Unordered lists
        r'\d+\.\s',  # Ordered lists
        r'\|.*\|',  # Tables
        r'---|___',  # Horizontal rules
    ]
    
    import re
    return any(re.search(pattern, text) for pattern in markdown_patterns)

def process_message_markdown(message: str) -> str:
    """Convert markdown syntax in message to HTML if markdown is present."""
    try:
        # If no markdown syntax is found, return the original message
        if not has_markdown_syntax(message):
            return message
            
        # Reset the markdown converter
        md.reset()
        # Convert markdown to HTML
        html = md.convert(message)
        return html
    except Exception as e:
        logger.error(f"Error processing markdown: {str(e)}")
        return message  # Return original message if markdown processing fails

@app.get("/")
async def home(
    request: Request,
    resources=Depends(get_resources),
    admin=Depends(get_current_admin),
):
    return templates.TemplateResponse(
        "dashboard.html",
        context={
            "request": request,
            "resources": resources,
            "resource_label": "Home",
            "page_pre_title": "overview",
            "page_title": "Home",
        },
    )

@app.get("/documents")
async def documents(
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
        collection = db.users_uploaded_docs
        
        # Calculate skip and limit for pagination
        skip = (page - 1) * per_page
        
        # Get total count for pagination
        total_docs = await collection.count_documents({})
        total_pages = (total_docs + per_page - 1) // per_page
        
        # Calculate pagination range
        start_page = max(1, page - 2)
        end_page = min(total_pages, page + 2)
        page_range = list(range(start_page, end_page + 1))
        
        # Calculate showing range
        start_showing = (page - 1) * per_page + 1
        end_showing = min(page * per_page, total_docs)
        
        # Use aggregation pipeline to get documents with their counts
        pipeline = [
            {
                "$project": {
                    "_id": 1,
                    "uuid": 1,
                    "num_documents": {"$size": {"$ifNull": ["$uploaded_documents", []]}},
                    "first_doc": {"$arrayElemAt": ["$uploaded_documents", 0]}
                }
            },
            {"$skip": skip},
            {"$limit": per_page}
        ]
        
        documents = []
        cursor = collection.aggregate(pipeline)
        
        async for doc in cursor:
            # Process the document to get required information
            username = "Unknown"
            if doc.get("first_doc") and doc["first_doc"].get("user_name"):
                username = doc["first_doc"]["user_name"]
            
            processed_doc = {
                "_id": str(doc["_id"]),
                "uuid": doc["uuid"],
                "username": username,
                "num_documents": doc["num_documents"]
            }
            documents.append(processed_doc)
            
        return templates.TemplateResponse(
            "documents.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "Documents",
                "page_pre_title": "User Documents",
                "page_title": "Documents",
                "documents": documents,
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
        # Log the error and return an error response
        logger.error(f"Error fetching documents: {str(e)}")
        return templates.TemplateResponse(
            "documents.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "Documents",
                "page_pre_title": "User Documents",
                "page_title": "Documents",
                "error": "Failed to fetch documents",
                "documents": [],
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

@app.get("/documents/{doc_id}")
async def user_documents(
    request: Request,
    doc_id: str,
    resources=Depends(get_resources),
    admin=Depends(get_current_admin),
    page: int = Query(1, ge=1),
    per_page: int = 10,
):
    try:            
        client = app.state.mongodb_client        
        # Get the database and collection
        db = client.API
        collection = db.users_uploaded_docs
        
        # Convert string ID to ObjectId
        try:
            object_id = ObjectId(doc_id)
        except Exception as e:
            logger.error(f"Invalid ObjectId format: {doc_id}")
            raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Invalid document ID format")
        
        # Find the document by ID
        doc = await collection.find_one({"_id": object_id})
        
        if not doc:
            logger.error(f"Document not found with ID: {doc_id}")
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Document not found")
        
        # Get username from the first document
        username = "Unknown"
        if doc.get("uploaded_documents") and doc["uploaded_documents"][0].get("user_name"):
            username = doc["uploaded_documents"][0]["user_name"]
        
        # Get total count for pagination
        total_docs = len(doc.get("uploaded_documents", []))
        total_pages = (total_docs + per_page - 1) // per_page
        
        # Calculate pagination range
        start_page = max(1, page - 2)
        end_page = min(total_pages, page + 2)
        page_range = list(range(start_page, end_page + 1))
        
        # Calculate showing range
        start_showing = (page - 1) * per_page + 1
        end_showing = min(page * per_page, total_docs)
        
        # Get paginated documents
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        documents = doc.get("uploaded_documents", [])[start_idx:end_idx]
        
        return templates.TemplateResponse(
            "user_documents.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "User Documents",
                "page_pre_title": "User Documents",
                "page_title": "User Documents",
                "username": username,
                "documents": documents,
                "uuid": doc.get("uuid"),
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
    except HTTPException as he:
        raise he
    except Exception as e:
        # Log the error and return an error response
        logger.error(f"Error fetching user documents: {str(e)}", exc_info=True)
        return templates.TemplateResponse(
            "user_documents.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "User Documents",
                "page_pre_title": "User Documents",
                "page_title": "User Documents",
                "username": "Unknown",
                "error": f"Failed to fetch documents: {str(e)}",
                "documents": [],
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

@app.put("/config/switch_status/{config_id}")
async def switch_config_status(
    request: Request, 
    config_id: int,
    admin=Depends(get_current_admin),
):
    config = await Config.get_or_none(pk=config_id)
    if not config:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND)
    config.status = not config.status
    await config.save(update_fields=["status"])
    return RedirectResponse(url=request.headers.get("referer"), status_code=HTTP_303_SEE_OTHER)

@app.get("/messages")
async def messages(
    request: Request,
    resources=Depends(get_resources),
    authorized=Depends(Permissions.CHAT_ACCESS),
    page: int = Query(1, ge=1),
    per_page: int = 10,
    user_id: str = Query(None),
    listing_id: str = Query(None),
    load_older: bool = Query(False),
):
    try:            
        client = app.state.mongodb_client        
        # Get the database and collections
        db = client.API
        messages_collection = db.messages
        users_collection = db.users
        
        # Calculate skip and limit for pagination
        skip = (page - 1) * per_page
        
        # Fetch contact list (always needed)
        pipeline = [
            {"$match": {"buyer_id": {"$exists": False}, "seller_id": {"$exists": False}}},
            {
                "$lookup": {
                    "from": "users",
                    "localField": "user_id",
                    "foreignField": "uuid",
                    "as": "user_info"
                }
            },
            {"$unwind": "$user_info"},
            {
                "$group": {
                    "_id": {
                        "user_id": "$user_id",
                        "listing_id": "$listing_id"
                    },
                    "user_name": {
                        "$first": {
                            "$concat": [
                                {"$ifNull": ["$user_info.first_name", ""]},
                                " ",
                                {"$ifNull": ["$user_info.last_name", ""]}
                            ]
                        }
                    },
                    "last_message": {
                        "$last": {
                            "$arrayElemAt": ["$messages", -1]
                        }
                    }
                }
            },
            {"$sort": {"last_message.timestamp": -1}},
            {"$skip": skip},
            {"$limit": per_page}
        ]
        
        # Get total unique user-listing combinations for pagination
        total_docs = await messages_collection.distinct(
            "user_id",
            {"buyer_id": {"$exists": False}, "seller_id": {"$exists": False}}
        )
        total_docs = len(total_docs)
        total_pages = (total_docs + per_page - 1) // per_page
        start_page = max(1, page - 2)
        end_page = min(total_pages, page + 2)
        page_range = list(range(start_page, end_page + 1))
        start_showing = (page - 1) * per_page + 1
        end_showing = min(page * per_page, total_docs)
        
        contacts = []
        cursor = messages_collection.aggregate(pipeline)
        
        async for doc in cursor:
            last_message = doc.get("last_message", {})
            contact_id = doc.get("_id", {})
            processed_doc = {
                "_id": str(contact_id.get("user_id", "")),
                "user_id": contact_id.get("user_id", ""),
                "listing_id": contact_id.get("listing_id", ""),
                "user_name": doc.get("user_name", "Unknown User").strip(),
                "last_message": {
                    "message": last_message.get("message", ""),
                    "timestamp": last_message.get("timestamp", ""),
                    "is_seen": last_message.get("is_seen", False),
                    "is_response": last_message.get("is_response", False)
                } if last_message else None
            }
            contacts.append(processed_doc)
        
        # If user_id is provided, fetch messages for that user
        if user_id:
            # Find the document for this user and listing combination
            query = {"user_id": user_id}
            if listing_id:
                query["listing_id"] = listing_id
                
            user_doc = await messages_collection.find_one(query)
            if not user_doc:
                raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="User messages not found")
            
            # Get user information
            user_info = await users_collection.find_one({"uuid": user_id})
            user_name = "Unknown User"
            user_email = "N/A"
            user_phone = "N/A"
            if user_info:
                first_name = user_info.get("first_name", "")
                last_name = user_info.get("last_name", "")
                user_name = f"{first_name} {last_name}".strip() or "Unknown User"
                user_email = user_info.get("email", "N/A")
                user_phone = user_info.get("phone", "N/A")
            
            # Get property information if listing_id exists
            property_info = None
            if listing_id:
                properties_collection = db.properties
                property_info = await properties_collection.find_one({"ListingId": listing_id})
            
            # Get messages array
            messages_array = user_doc.get("messages", [])
            total_messages = len(messages_array)
            
            # Sort messages by timestamp in ascending order (oldest first)
            messages_array.sort(key=lambda x: x.get("timestamp", ""))
            
            if load_older:
                # If loading older messages, get the next 25 messages before the oldest message we have
                oldest_timestamp = messages_array[0].get("timestamp") if messages_array else None
                if oldest_timestamp:
                    older_messages = [msg for msg in user_doc.get("messages", []) 
                                    if msg.get("timestamp", "") < oldest_timestamp]
                    older_messages.sort(key=lambda x: x.get("timestamp", ""))
                    messages_array = older_messages[:25] + messages_array
            
            # If not loading older messages, get the last 25 messages
            if not load_older:
                messages_array = messages_array[-25:]
            
            # Process markdown in messages
            for message in messages_array:
                if message.get("message"):
                    message["message"] = process_message_markdown(message.get("message", ""))
                # Ensure media path is properly formatted
                if message.get("media"):
                    # Remove any leading slash to ensure proper URL construction
                    message["media"] = message["media"].lstrip("/")
            
            # Mark last message as read if it's not a response and not already seen
            if messages_array and not messages_array[-1].get("is_response") and not messages_array[-1].get("is_seen"):
                try:
                    message_id = messages_array[-1].get("message_id")  # Get the message ID
                    filter_doc = {
                        "user_id": user_id,
                        "messages": {
                            "$elemMatch": {
                                "message_id": message_id,
                                "is_seen": False
                            }
                        }
                    }
                    if listing_id:
                        filter_doc["listing_id"] = listing_id
                        
                    update_doc = {
                        "$set": {
                            "messages.$[elem].is_seen": True
                        }
                    }
                    
                    array_filters = [{"elem.message_id": message_id}]
                    
                    result = await messages_collection.update_one(
                        filter_doc, 
                        update_doc,
                        array_filters=array_filters
                    )
                    if result.modified_count > 0:
                        messages_array[-1]["is_seen"] = True
                        logger.info(f"Message {message_id} marked as read in database")
                except Exception as e:
                    logger.error(f"Error marking message as read: {str(e)}")
            
            return templates.TemplateResponse(
                "messages.html",
                context={
                    "request": request,
                    "resources": resources,
                    "resource_label": "Messages",
                    "page_pre_title": "User Messages",
                    "page_title": f"Messages with {user_name}",
                    "selected_user": {
                        "id": user_id,
                        "name": user_name,
                        "email": user_email,
                        "phone": user_phone,
                        "listing_id": listing_id,
                        "property": {
                            "address": property_info.get("address", "N/A") if property_info else None,
                            "price": property_info.get("price", "N/A") if property_info else None,
                            "beds": property_info.get("beds", "N/A") if property_info else None,
                            "baths": property_info.get("baths", "N/A") if property_info else None,
                            "built_in": property_info.get("built_in", "N/A") if property_info else None,
                            "size": property_info.get("size", "N/A") if property_info else None
                        } if property_info else None
                    },
                    "messages": messages_array,
                    "contacts": contacts,
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
                    },
                    "has_older_messages": any(msg.get("timestamp", "") < messages_array[0].get("timestamp", "") for msg in user_doc.get("messages", [])) if messages_array else False
                },
            )
        
        # If no user_id, show only the contacts list
        return templates.TemplateResponse(
            "messages.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "Messages",
                "page_pre_title": "User Messages",
                "page_title": "Messages",
                "contacts": contacts,
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
        logger.error(f"Error fetching messages: {str(e)}")
        return templates.TemplateResponse(
            "messages.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "Messages",
                "page_pre_title": "User Messages",
                "page_title": "Messages",
                "error": "Failed to fetch messages",
                "messages": [],
                "contacts": [],
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

@app.post("/messages/send")
async def send_message(
    request: Request,
    authorized=Depends(Permissions.CHAT_ACCESS),
    user_id: str = Form(...),
    listing_id: str = Form(None),
    message: str = Form(None),
    file: UploadFile = File(None),
):
    try:
        # Redirect back to the messages page
        redirect_url = f"/admin/messages?user_id={user_id}{f'&listing_id={listing_id}' if listing_id != 'None' else ''}"
        
        # Prepare the form data
        form_data = {}
        if message and message.strip():
            form_data["message"] = message.strip()
        form_data["user_id"] = user_id
        if listing_id:
            form_data["listing_id"] = listing_id
        
        # Make the API call to send the message
        api_url = "https://api.airebrokers.com/project-api/api1/user/customerservicereply"
        headers = {
            "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJmcmVzaCI6ZmFsc2UsImlhdCI6MTc0MzkwODQ4NCwianRpIjoiNTk4MDk4NDctZGM2NS00YTRiLTk2NjAtNGZmZWE1Mjc1NDM2IiwidHlwZSI6ImFjY2VzcyIsInN1YiI6ImZlZGlzbGltZW45OEBnbWFpbC5jb20iLCJuYmYiOjE3NDM5MDg0ODQsImNzcmYiOiI3N2FlNWFlMi04ZDBjLTRmN2ItYTk1MC00MjYxNDIyNWEzMjMiLCJleHAiOjE3NDUyMDQ0ODR9.yWdoGWl8bxNLyqXOtA7fCZpzv4dyJc8yossM8qly8zU"
        }
        
        async with httpx.AsyncClient() as client:
            if file:
                # If there's a file, send as multipart form
                files = {"media_file": (file.filename, file.file, file.content_type)}
                response = await client.post(api_url, headers=headers, data=form_data, files=files)
            else:
                # If no file, send as regular form data
                response = await client.post(api_url, headers=headers, data=form_data)
            
            response.raise_for_status()
            
        return RedirectResponse(url=redirect_url, status_code=HTTP_303_SEE_OTHER)
        
    except Exception as e:
        logger.error(f"Error sending message: {str(e)}")
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to send message")

@app.get("/notifications")
async def notifications(
    request: Request,
    authorized=Depends(Permissions.MANAGE_NOTIFICATIONS),
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

@app.post("/notifications/send")
async def send_notification(
    request: Request,
    authorized=Depends(Permissions.MANAGE_NOTIFICATIONS),
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

@app.get("/yaml-editor")
async def yaml_editor(
    request: Request,
    resources=Depends(get_resources),
    admin=Depends(get_current_admin),
    file_path: str = Query(None),
):
    try:
        # Initialize variables
        yaml_content = ""
        yaml_files = {}
        base_path = "yamls"
        
        # Scan the yamls directory for cities and their YAML files
        try:
            for city in os.listdir(base_path):
                city_path = os.path.join(base_path, city)
                if os.path.isdir(city_path):
                    yaml_files[city] = []
                    for file in os.listdir(city_path):
                        if file.endswith(('.yaml', '.yml')):
                            yaml_files[city].append(file)
        except FileNotFoundError:
            yaml_files = {}
            
        # If a specific file is requested, load its content
        if file_path:
            try:
                with open(file_path, "r") as f:
                    yaml_content = f.read()
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail="YAML file not found")
        
        return templates.TemplateResponse(
            "yaml-editor.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "YAML Editor",
                "page_pre_title": "Contract Template Questions",
                "page_title": "YAML Editor",
                "yaml_content": yaml_content,
                "yaml_files": yaml_files,
                "selected_file": file_path,
            },
        )
    except Exception as e:
        logger.error(f"Error accessing YAML editor: {str(e)}")
        return templates.TemplateResponse(
            "yaml-editor.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "YAML Editor",
                "page_pre_title": "Contract Template Questions",
                "page_title": "YAML Editor",
                "error": f"Failed to load YAML editor: {str(e)}",
                "yaml_content": "",
                "yaml_files": {},
                "selected_file": None,
            },
        )

@app.post("/yaml-editor/save")
async def save_yaml(
    request: Request,
    yaml_content: str = Form(...),
    file_path: str = Form(...),
    admin=Depends(get_current_admin),
):
    try:
        # Validate YAML syntax
        import yaml
        try:
            yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            raise HTTPException(status_code=400, detail=f"Invalid YAML syntax: {str(e)}")

        # Ensure the directory exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # Remove any trailing spaces and normalize line endings
        cleaned_content = "\n".join(line.rstrip() for line in yaml_content.splitlines())
        
        # Save to file with normalized line endings
        with open(file_path, "w", newline="\n") as f:
            f.write(cleaned_content)

        return RedirectResponse(
            url=f"/admin/yaml-editor?file_path={file_path}",
            status_code=HTTP_303_SEE_OTHER
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error saving YAML: {str(e)}")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save YAML: {str(e)}"
        )

@app.get("/users/search")
async def search_users(
    request: Request,
    authorized=Depends(Permissions.VIEW_USERS),
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

@app.get("/id-verification")
async def id_verification(
    request: Request,
    authorized=Depends(Permissions.MANAGE_USERS),
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

@app.post("/id-verification/update")
async def update_verification(
    request: Request,
    authorized=Depends(Permissions.MANAGE_USERS),
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


@app.post("/property_verification")
async def property_verification(
    request: Request,
    authorized=Depends(Permissions.MANAGE_PROPERTIES),
    property_id: str = Form(...),
    version_id: str = Form(...),
    verification_id: str = Form(...),
    action: str = Form(...),  # approve or reject
    rejection_reason: str = Form(None),
    admin=Depends(get_current_admin),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        # Get the verification queue item
        verification = await db.verification_queue.find_one({
            "_id": ObjectId(verification_id),
            "status": "pending"
        })

        if not verification:
            raise HTTPException(status_code=404, detail="Verification request not found")
            
        # Get the property version
        version = await db.property_versions.find_one({
            "_id": ObjectId(version_id),
        })

        if not version:
            raise HTTPException(status_code=404, detail="Property version not found")
            
        # Get the property
        property = await db.properties.find_one({"_id": ObjectId(property_id)})
        if not property:
            raise HTTPException(status_code=404, detail="Property not found")
            
        # Update verification queue status
        await db.verification_queue.update_one(
            {"_id": ObjectId(verification["_id"])},
            {
                "$set": {
                    "status": action,
                    "processed_at": datetime.now(),
                    "processed_by": admin.username,
                    "rejection_reason": rejection_reason if action == "reject" else None
                }
            }
        )
        
        # Update property version status
        await db.property_versions.update_one(
            {"_id": ObjectId(version_id)},
            {
                "$set": {
                    "status": action,
                    "processed_at": datetime.now(),
                    "processed_by": admin.username,
                    "rejection_reason": rejection_reason if action == "reject" else None,
                    "reviewed_at": datetime.now()
                }
            }
        )
        
        if action == "approve":
            # Update the property with the new version data
            update_data = {k.strip('"'): v for k, v in version["full_snapshot"].items()}
            update_data["is_published"] = True
            update_data["status"] = "approved"
            update_data["current_version_id"] = version_id
            update_data["updated_at"] = datetime.now()
            
            await db.properties.update_one(
                {"_id": ObjectId(property_id)},
                {"$set": update_data}
            )
            
            # Create audit log entry
            audit_entry = {
                "timestamp": datetime.now(),
                "admin_username": admin.username,
                "action": "property_verification_approve",
                "property_id": property_id,
                "version_id": version_id,
                "details": {
                    "type": verification["type"],
                    "previous_status": property["status"],
                    "new_status": "active",
                    "changes": {
                        "fields_updated": list(update_data.keys()),
                        "previous_version": property["current_version_id"]
                    }
                }
            }
        else:  # reject
            # Create audit log entry
            audit_entry = {
                "timestamp": datetime.now(),
                "admin_username": admin.username,
                "action": "property_verification_reject",
                "property_id": property_id,
                "version_id": version_id,
                "details": {
                    "type": verification["type"],
                    "rejection_reason": rejection_reason,
                    "previous_status": property["status"],
                    "new_status": "rejected"
                }
            }
            
            # Update property status to rejected
            await db.properties.update_one(
                {"_id": ObjectId(property_id)},
                {
                    "$set": {
                        "status": "rejected",
                        "updated_at": datetime.now()
                    }
                }
            )
            
        # Send notification to user
        if version.get("submitted_by"):
            user = await db.users.find_one({"uuid": version["submitted_by"]})
            if user and user.get("device_token"):
                # Set notification text based on action and verification type
                action_text = "approved" if action == "approve" else "rejected"
                update_type = "new listing" if verification["type"] == "new_listing" else "property update"
                
                title = f"{update_type.title()} {action_text.title()}"
                message = f"Your {update_type} for listing #{property.get('ListingId', '')} has been {action_text}"
                if action == "reject" and rejection_reason:
                    message += f" - {rejection_reason}"

                fcm_service = FCMService()
                asyncio.create_task(fcm_service.send_notification(
                    tokens=[user["device_token"]],
                    title=title,
                    body=message,
                    data={"property_id": property_id},
                    notification_id=str(verification["_id"]),
                    db_client=client
                ))
                
                # Create user notification
                await db.user_notifications.insert_one({
                    "user_id": version["submitted_by"],
                    "notification_id": verification["_id"],
                    "title": title,
                    "message": message,
                    "created_at": datetime.now(),
                    "is_read": False,
                    "type": "property_update",
                    "status": "sent",
                    "metadata": {
                        "property_id": property_id,
                        "version_id": version_id,
                        "admin_username": admin.username
                    }
                })
        # Save audit log
        await db.verification_audit_log.insert_one(audit_entry)
        
        return RedirectResponse(url="/admin/property-verification", status_code=HTTP_303_SEE_OTHER)
        
    except Exception as e:
        logger.error(f"Error processing property verification: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to process property verification")

@app.get("/property-verification")
async def view_property_verification(
    request: Request,
    authorized=Depends(Permissions.MANAGE_PROPERTIES),
    resources=Depends(get_resources),
    admin=Depends(get_current_admin),
    page: int = Query(1, ge=1),
    per_page: int = 10,
    type: str = Query("new_listing", regex="^(new_listing|edit)$"),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        
        # Calculate skip and limit for pagination
        skip = (page - 1) * per_page
        
        # Create aggregation pipeline
        pipeline = [
            # Match verification queue items
            {
                "$match": {
                    "status": "pending",
                    "type": type
                }
            },
            # Convert string IDs to ObjectId
            {
                "$addFields": {
                    "property_id_obj": {"$toObjectId": "$property_id"},
                    "version_id_obj": {"$toObjectId": "$version_id"}
                }
            },
            # Sort by creation date
            {
                "$sort": {
                    "created_at": -1
                }
            },
            # Lookup property version
            {
                "$lookup": {
                    "from": "property_versions",
                    "localField": "version_id_obj",
                    "foreignField": "_id",
                    "as": "version"
                }
            },
            # Unwind version array
            {
                "$unwind": "$version"
            },
            # Lookup property
            {
                "$lookup": {
                    "from": "properties",
                    "localField": "property_id_obj",
                    "foreignField": "_id",
                    "as": "property"
                }
            },
            # Unwind property array
            {
                "$unwind": "$property"
            },
            # Lookup user who submitted
            {
                "$lookup": {
                    "from": "users",
                    "localField": "version.submitted_by",
                    "foreignField": "uuid",
                    "as": "submitted_by"
                }
            },
            # Unwind submitted_by array
            {
                "$unwind": {
                    "path": "$submitted_by",
                    "preserveNullAndEmptyArrays": True
                }
            },
            # Project required fields
            {
                "$project": {
                    "_id": 1,
                    "property_id": 1,
                    "version_id": 1,
                    "type": 1,
                    "created_at": 1,
                    "property": {
                        "ListingId": 1,
                        "ParcelNumber": 1,
                        "beds": 1,
                        "baths": 1,
                        "full_bathrooms": 1,
                        "half_bathrooms": 1,
                        "Cooling": 1,
                        "Heating": 1,
                        "LotSizeAcres": 1,
                        "type": 1,
                        "YearBuilt": 1,
                        "ZoningDescription": 1,
                        "TaxLegalDescription": 1,
                        "AssociationFee": 1,
                        "InternetAddressDisplayYN": 1,
                        "SubdivisionName": 1,
                        "TaxAnnualAmount": 1,
                        "StandardStatus": 1,
                        "ListingContractDate": 1,
                        "PostalCode": 1,
                        "latitude": 1,
                        "longitude": 1,
                        "City": 1,
                        "StateOrProvince": 1,
                        "CountyOrParish": 1,
                        "StreetName": 1,
                        "StreetNumber": 1,
                        "StreetSuffix": 1,
                        "address": 1,
                        "LivingArea": 1,
                        "ParkingFeatures": 1,
                        "PropertySubType": 1,
                        "Sewer": 1,
                        "WaterSource": 1,
                        "WaterfrontYN": 1,
                        "construction": 1,
                        "materials": 1,
                        "Utilities": 1,
                        "VirtualTourURLUnbranded": 1,
                        "OriginatingSystemName": 1,
                        "attached_garage": 1,
                        "size": 1,
                        "built_in": 1,
                        "price": 1,
                        "description": 1,
                        "images": 1,
                        "panoramic_images": 1,
                        "appliances": 1,
                        "kitchen_features": 1,
                        "features": 1,
                        "type_and_styles": 1,
                        "created_at": 1,
                        "updated_at": 1,
                        "available_viewing_times": 1,
                        "open_house_times": 1,
                        "status": 1,
                        "current_version_id": 1,
                        "is_published": 1
                    },
                    "version": {
                        "version_number": 1,
                        "full_snapshot": 1,
                        "submitted_by": 1,
                        "created_at": 1
                    },
                    "submitted_by": {
                        "first_name": 1,
                        "last_name": 1,
                        "email": 1
                    }
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
        result = await db.verification_queue.aggregate(pipeline).to_list(length=1)        
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
            "resource_label": "Property Verification",
            "page_pre_title": "Property Verification",
            "page_title": "Property Verification",
            "verifications": verifications,
            "current_type": type,
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
            "property-verification.html",
            context=context
        )
    except Exception as e:
        logger.error(f"Error in property verification route: {str(e)}", exc_info=True)
        return templates.TemplateResponse(
            "property-verification.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "Property Verification",
                "page_pre_title": "Property Verification",
                "page_title": "Property Verification",
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

@app.get("/property-verification/{verification_id}")
async def get_property_verification_details(
    request: Request,
    verification_id: str,
    authorized=Depends(Permissions.MANAGE_PROPERTIES),
    admin=Depends(get_current_admin),
    resources=Depends(get_resources),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        
        # Convert string IDs to ObjectId
        try:
            verification_id_obj = ObjectId(verification_id)
        except Exception as e:
            logger.error(f"Invalid ObjectId format: {verification_id}")
            raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Invalid verification ID format")
        
        # Get verification queue item
        verification = await db.verification_queue.find_one({"_id": verification_id_obj})
        if not verification:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Verification request not found")
        # Get the property version
        version = await db.property_versions.find_one({
            "_id": ObjectId(verification["version_id"]),
        })
        
        if not version:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Property version not found")
            
        # Get the current property
        property = await db.properties.find_one({"_id": ObjectId(verification["property_id"])})
        if not property:
            raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Property not found")
            
        # Get current version if exists
        current_version = None
        if property.get("current_version_id"):
            current_version = await db.property_versions.find_one({
                "_id": ObjectId(property["current_version_id"])
            })

        # Get user who submitted the verification
        submitted_by = None
        if version.get("submitted_by"):
            user = await db.users.find_one({"uuid": version["submitted_by"]})
            if user:
                submitted_by = {
                    "name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip(),
                    "email": user.get("email", "")
                }

        context = {
            "request": request,
            "resources": resources,
            "resource_label": "Property Verification",
            "page_pre_title": "Property Verification",
            "page_title": "Property Verification Details",
            "verification_id": verification_id,
            "property_id": verification["property_id"],
            "version_id": verification["version_id"],
            "current_version": current_version["full_snapshot"] if current_version else property,
            "proposed_version": version["full_snapshot"],
            "submitted_by": submitted_by,
            "submitted_at": version.get("created_at"),
            "verification_type": verification["type"]
        }
        
        return templates.TemplateResponse(
            "property-verification-details.html",
            context=context
        )
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error fetching verification details: {str(e)}")
        return templates.TemplateResponse(
            "property-verification-details.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "Property Verification",
                "page_pre_title": "Property Verification",
                "page_title": "Property Verification Details",
                "error": f"Failed to load verification details: {str(e)}"
            }
        )

@app.get("/user-management")
async def user_management(
    request: Request,
    authorized=Depends(Permissions.MANAGE_USERS),
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

@app.get("/user-management/{user_uuid}")
async def view_user_profile(
    request: Request,
    user_uuid: str,
    authorized=Depends(Permissions.MANAGE_USERS),
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

@app.post("/user-management/{user_uuid}/update-info")
async def update_user_info(
    request: Request,
    user_uuid: str,
    authorized=Depends(Permissions.MANAGE_USERS),
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

@app.post("/user-management/{user_uuid}/verify")
async def update_verification(
    request: Request,
    user_uuid: str,
    authorized=Depends(Permissions.MANAGE_USERS),
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

@app.get("/app-version-management")
async def app_version_management(
    request: Request,
    resources=Depends(get_resources),
    admin=Depends(get_current_admin),
    platform: str = Query(None, regex="^(android|ios|None)$"),
    status: str = Query(None, regex="^(latest|prompted|deprecated|None)$"),
    page: int = Query(1, ge=1),
    per_page: int = 10,
):
    try:
        client = app.state.mongodb_client
        db = client.API
        
        # Calculate skip and limit for pagination
        skip = (page - 1) * per_page
        
        # Build match conditions
        match_conditions = {}
        
        if platform and platform != "None":
            match_conditions["platform"] = platform
            
        if status and status != "None":
            match_conditions["status"] = status
        
        # Get total count for pagination
        total_docs = await db.app_versions.count_documents(match_conditions)
        total_pages = (total_docs + per_page - 1) // per_page
        
        # Calculate pagination range
        start_page = max(1, page - 2)
        end_page = min(total_pages, page + 2)
        page_range = list(range(start_page, end_page + 1))
        
        # Calculate showing range
        start_showing = (page - 1) * per_page + 1
        end_showing = min(page * per_page, total_docs)
        
        # Fetch versions with pagination
        cursor = db.app_versions.find(match_conditions).sort("created_at", -1).skip(skip).limit(per_page)
        versions = []
        async for doc in cursor:
            versions.append({
                "_id": str(doc["_id"]),
                "version": doc.get("version", ""),
                "platform": doc.get("platform", ""),
                "status": doc.get("status", ""),
                "release_notes": doc.get("release_notes", ""),
                "created_at": doc.get("created_at", datetime.now())
            })
        
        context = {
            "request": request,
            "resources": resources,
            "resource_label": "App Version Management",
            "page_pre_title": "App Version Control",
            "page_title": "Version Management",
            "versions": versions,
            "current_platform": platform if platform != "None" else None,
            "current_status": status if status != "None" else None,
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
            "app-version-management.html",
            context=context
        )
    except Exception as e:
        logger.error(f"Error in app version management route: {str(e)}")
        return templates.TemplateResponse(
            "app-version-management.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "App Version Management",
                "page_pre_title": "App Version Control",
                "page_title": "Version Management",
                "error": f"Failed to load versions: {str(e)}",
                "versions": [],
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

@app.post("/app-version-management/add")
async def add_app_version(
    request: Request,
    version: str = Form(...),
    platform: str = Form(...),
    status: str = Form(...),
    release_notes: str = Form(...),
    admin=Depends(get_current_admin),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        
        # Validate version format (e.g., 1.2.0)
        import re
        if not re.match(r'^\d+\.\d+\.\d+$', version):
            raise HTTPException(status_code=400, detail="Invalid version format. Use format: X.Y.Z")
            
        # Validate platform
        if platform not in ["android", "ios"]:
            raise HTTPException(status_code=400, detail="Invalid platform")
            
        # Validate status
        if status not in ["latest", "prompted", "deprecated"]:
            raise HTTPException(status_code=400, detail="Invalid status")
            
        # Create new version document
        version_doc = {
            "version": version,
            "platform": platform,
            "status": status,
            "release_notes": release_notes,
            "created_at": datetime.now()
        }
        
        # Insert into database
        await db.app_versions.insert_one(version_doc)
        
        # Create audit log entry
        audit_entry = {
            "timestamp": datetime.now(),
            "admin_username": admin.username,
            "action": "add_app_version",
            "details": version_doc
        }
        
        # Save audit log
        await db.audit_log.insert_one(audit_entry)
        
        return RedirectResponse(url="/admin/app-version-management", status_code=HTTP_303_SEE_OTHER)
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error adding app version: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to add app version")

@app.post("/app-version-management/{version_id}/update")
async def update_app_version(
    request: Request,
    version_id: str,
    status: str = Form(...),
    release_notes: str = Form(...),
    admin=Depends(get_current_admin),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        
        # Validate status
        if status not in ["latest", "prompted", "deprecated"]:
            raise HTTPException(status_code=400, detail="Invalid status")
            
        # Update version document
        update_data = {
            "status": status,
            "release_notes": release_notes,
            "updated_at": datetime.now()
        }
        
        result = await db.app_versions.update_one(
            {"_id": ObjectId(version_id)},
            {"$set": update_data}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Version not found")
            
        # Create audit log entry
        audit_entry = {
            "timestamp": datetime.now(),
            "admin_username": admin.username,
            "action": "update_app_version",
            "version_id": version_id,
            "details": update_data
        }
        
        # Save audit log
        await db.audit_log.insert_one(audit_entry)
        
        return RedirectResponse(url="/admin/app-version-management", status_code=HTTP_303_SEE_OTHER)
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error updating app version: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update app version")

@app.post("/app-version-management/{version_id}/delete")
async def delete_app_version(
    request: Request,
    version_id: str,
    admin=Depends(get_current_admin),
):
    try:
        client = app.state.mongodb_client
        db = client.API
        
        # Delete version document
        result = await db.app_versions.delete_one({"_id": ObjectId(version_id)})
        
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Version not found")
            
        # Create audit log entry
        audit_entry = {
            "timestamp": datetime.now(),
            "admin_username": admin.username,
            "action": "delete_app_version",
            "version_id": version_id
        }
        
        # Save audit log
        await db.audit_log.insert_one(audit_entry)
        
        return RedirectResponse(url="/admin/app-version-management", status_code=HTTP_303_SEE_OTHER)
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error deleting app version: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete app version")

@app.get("/showing-requests")
async def showing_requests(
    request: Request,
    authorized=Depends(Permissions.MANAGE_USERS),
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

@app.post("/showing-requests/{request_id}/process")
async def process_showing_request(
    request: Request,
    request_id: str,
    authorized=Depends(Permissions.MANAGE_USERS),
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

@app.get("/agents/search")
async def search_agents(
    request: Request,
    authorized=Depends(Permissions.VIEW_USERS),
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

@app.get("/properties/search")
async def search_properties(
    request: Request,
    authorized=Depends(Permissions.VIEW_PROPERTIES),
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

@app.post("/showing-requests/export")
async def export_showing_requests(
    request: Request,
    authorized=Depends(Permissions.MANAGE_USERS),
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

@app.get("/settings")
async def settings(
    request: Request,
    authorized=Depends(Permissions.MANAGE_GROUPS),
    resources=Depends(get_resources),
    admin=Depends(get_current_admin),
    tab: str = Query("users", regex="^(users|groups)$"),
    page: int = Query(1, ge=1),
    per_page: int = 10,
):
    try:
        total_docs = 0
        items = []
        
        if tab == "users":
            # Get total users count
            total_docs = await Admin.all().count()
            
            # Get paginated users with their groups
            items = await Admin.all().offset((page - 1) * per_page).limit(per_page).prefetch_related('group')
            
            # Format user data
            items = [{
                "id": user.pk,
                "username": user.username,
                "email": user.email,
                "last_login": user.last_login,
                "created_at": user.created_at,
                "group": {
                    "id": user.group.pk,
                    "name": user.group.name
                } if user.group else None
            } for user in items]
            
        else:  # groups tab
            # Get total groups count
            total_docs = await Groups.all().count()
            
            # Get paginated groups with their admins
            items = await Groups.all().offset((page - 1) * per_page).limit(per_page).prefetch_related('admins')
            
            # Format group data
            items = [{
                "id": group.pk,
                "name": group.name,
                "description": group.description,
                "is_active": group.is_active,
                "created_at": group.created_at,
                "permissions": {
                    "can_view_users": group.can_view_users,
                    "can_manage_users": group.can_manage_users,
                    "can_chat_users": group.can_chat_users,
                    "can_manage_properties": group.can_manage_properties,
                    "can_manage_showing_requests": group.can_manage_showing_requests,
                    "can_manage_groups": group.can_manage_groups,
                    "can_view_audit_logs": group.can_view_audit_logs,
                    "can_manage_notifications": group.can_manage_notifications,
                    "can_view_properties": group.can_view_properties
                },
                "admin_count": len(group.admins)
            } for group in items]

        # Calculate pagination values
        total_pages = (total_docs + per_page - 1) // per_page
        start_page = max(1, page - 2)
        end_page = min(total_pages, page + 2)
        page_range = list(range(start_page, end_page + 1))
        start_showing = (page - 1) * per_page + 1
        end_showing = min(page * per_page, total_docs)

        # Get all groups for user creation/editing
        all_groups = []
        if tab == "users":
            all_groups = await Groups.filter(is_active=True).all()
            all_groups = [{
                "id": group.pk,
                "name": group.name
            } for group in all_groups]

        context = {
            "request": request,
            "resources": resources,
            "resource_label": "Settings",
            "page_pre_title": "System Settings",
            "page_title": "User & Group Management",
            "current_tab": tab,
            "items": items,
            "all_groups": all_groups,
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
            "settings.html",
            context=context
        )
    except Exception as e:
        logger.error(f"Error in settings route: {str(e)}", exc_info=True)
        return templates.TemplateResponse(
            "settings.html",
            context={
                "request": request,
                "resources": resources,
                "resource_label": "Settings",
                "page_pre_title": "System Settings",
                "page_title": "User & Group Management",
                "error": f"Failed to load settings: {str(e)}",
                "current_tab": tab,
                "items": [],
                "all_groups": [],
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

@app.post("/settings/users/add")
async def add_user(
    request: Request,
    authorized=Depends(Permissions.MANAGE_GROUPS),
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    group_id: int = Form(...),  # Changed from Form(None) to Form(...)
    admin=Depends(get_current_admin),
):
    try:
        # Check if group exists
        group = await Groups.get_or_none(pk=group_id)
        if not group:
            raise HTTPException(status_code=400, detail="Selected group does not exist")

        # Check if username already exists
        if await Admin.filter(username=username).exists():
            raise HTTPException(status_code=400, detail="Username already exists")

        # Create new user
        await Admin.create(
            username=username,
            email=email,
            password=password,
            group=group  # Ensure group is assigned
        )
        return RedirectResponse(url="/admin/settings?tab=users", status_code=HTTP_303_SEE_OTHER)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error adding user: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to add user")

@app.post("/settings/users/{user_id}/edit")
async def edit_user(
    request: Request,
    user_id: int,
    authorized=Depends(Permissions.MANAGE_GROUPS),
    email: str = Form(...),
    password: str = Form(None),
    group_id: int = Form(...),  # Changed from Form(None) to Form(...)
    admin=Depends(get_current_admin),
):
    try:
        # Get user
        user = await Admin.get_or_none(pk=user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Check if group exists
        group = await Groups.get_or_none(pk=group_id)
        if not group:
            raise HTTPException(status_code=400, detail="Selected group does not exist")

        # Update user
        user.email = email
        user.group = group  # Ensure group is assigned
        if password:
            user.password = password  # Will be hashed by pre_save signal
        await user.save()
        return RedirectResponse(url="/admin/settings?tab=users", status_code=HTTP_303_SEE_OTHER)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error editing user: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to edit user")

@app.post("/settings/users/{user_id}/delete")
async def delete_user(
    request: Request,
    user_id: int,
    authorized=Depends(Permissions.MANAGE_GROUPS),
    admin=Depends(get_current_admin),
):
    try:
        # Get user
        user = await Admin.get_or_none(pk=user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Don't allow deleting yourself
        if user.pk == admin.pk:
            raise HTTPException(status_code=400, detail="Cannot delete your own account")

        await user.delete()
        return RedirectResponse(url="/admin/settings?tab=users", status_code=HTTP_303_SEE_OTHER)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error deleting user: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete user")

@app.post("/settings/groups/add")
async def add_group(
    request: Request,
    authorized=Depends(Permissions.MANAGE_GROUPS),
    name: str = Form(...),
    description: str = Form(None),
    can_view_users: bool = Form(False),
    can_manage_users: bool = Form(False),
    can_chat_users: bool = Form(False),
    can_manage_properties: bool = Form(False),
    can_manage_showing_requests: bool = Form(False),
    can_manage_groups: bool = Form(False),
    can_view_audit_logs: bool = Form(False),
    can_manage_notifications: bool = Form(False),
    can_view_properties: bool = Form(False),
    admin=Depends(get_current_admin),
):
    try:
        # Check if group name exists
        if await Groups.filter(name=name).exists():
            raise HTTPException(status_code=400, detail="Group name already exists")

        # Create new group
        await Groups.create(
            name=name,
            description=description,
            can_view_users=can_view_users,
            can_manage_users=can_manage_users,
            can_chat_users=can_chat_users,
            can_manage_properties=can_manage_properties,
            can_manage_showing_requests=can_manage_showing_requests,
            can_manage_groups=can_manage_groups,
            can_view_audit_logs=can_view_audit_logs,
            can_manage_notifications=can_manage_notifications,
            can_view_properties=can_view_properties
        )

        return RedirectResponse(url="/admin/settings?tab=groups", status_code=HTTP_303_SEE_OTHER)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error adding group: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to add group")

@app.post("/settings/groups/{group_id}/edit")
async def edit_group(
    request: Request,
    group_id: int,
    authorized=Depends(Permissions.MANAGE_GROUPS),
    name: str = Form(...),
    description: str = Form(None),
    can_view_users: bool = Form(False),
    can_manage_users: bool = Form(False),
    can_chat_users: bool = Form(False),
    can_manage_properties: bool = Form(False),
    can_manage_showing_requests: bool = Form(False),
    can_manage_groups: bool = Form(False),
    can_view_audit_logs: bool = Form(False),
    can_manage_notifications: bool = Form(False),
    is_active: bool = Form(True),
    can_view_properties: bool = Form(False),
    admin=Depends(get_current_admin),
):
    try:
        # Get group
        group = await Groups.get_or_none(pk=group_id)
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")

        # Check if new name conflicts with existing groups
        if name != group.name and await Groups.filter(name=name).exists():
            raise HTTPException(status_code=400, detail="Group name already exists")

        # Update group
        group.name = name
        group.description = description
        group.can_view_users = can_view_users
        group.can_manage_users = can_manage_users
        group.can_chat_users = can_chat_users
        group.can_manage_properties = can_manage_properties
        group.can_manage_showing_requests = can_manage_showing_requests
        group.can_manage_groups = can_manage_groups
        group.can_view_audit_logs = can_view_audit_logs
        group.can_manage_notifications = can_manage_notifications
        group.is_active = is_active
        group.can_view_properties = can_view_properties 

        await group.save()
        return RedirectResponse(url="/admin/settings?tab=groups", status_code=HTTP_303_SEE_OTHER)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error editing group: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to edit group")

@app.post("/settings/groups/{group_id}/delete")
async def delete_group(
    request: Request,
    group_id: int,
    authorized=Depends(Permissions.MANAGE_GROUPS),
    admin=Depends(get_current_admin),
):
    try:
        # Get group
        group = await Groups.get_or_none(pk=group_id)
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")

        # Don't allow deleting group if it has users
        if await Admin.filter(group=group).exists():
            raise HTTPException(status_code=400, detail="Cannot delete group with assigned users")

        await group.delete()
        return RedirectResponse(url="/admin/settings?tab=groups", status_code=HTTP_303_SEE_OTHER)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error deleting group: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete group")

@app.get("/triggers")
async def triggers(
    request: Request,
    authorized=Depends(Permissions.MANAGE_GROUPS),
    resources=Depends(get_resources),
    admin=Depends(get_current_admin),
    page: int = Query(1, ge=1),
    per_page: int = 10,
    show_logs: bool = Query(False),
):
    """View triggers list and execution logs"""
    try:
        db = request.app.state.mongodb_client.API
        skip = (page - 1) * per_page

        # Get triggers data
        triggers_cursor = db.triggers.find().skip(skip).limit(per_page)
        triggers = await triggers_cursor.to_list(length=None)
        total_triggers = await db.triggers.count_documents({})

        # Get logs data if needed
        trigger_logs = []
        total_logs = 0
        if show_logs:
            logs_cursor = db.trigger_logs.find().sort("timestamp", -1).skip(skip).limit(per_page)
            trigger_logs = await logs_cursor.to_list(length=None)
            total_logs = await db.trigger_logs.count_documents({})

        has_next = (skip + per_page) < total_triggers
        has_prev = page > 1

        logs_has_next = (skip + per_page) < total_logs if show_logs else False
        logs_has_prev = page > 1 if show_logs else False

        return templates.TemplateResponse(
            "triggers.html",
            {
                "request": request,
                "resources": resources,
                "admin": admin,
                "triggers": triggers,
                "total_triggers": total_triggers,
                "has_next": has_next,
                "has_prev": has_prev,
                "page": page,
                "show_logs": show_logs,
                "trigger_logs": trigger_logs,
                "total_logs": total_logs,
                "logs_has_next": logs_has_next,
                "logs_has_prev": logs_has_prev,
                "logs_page": page,
                "event_types": EVENT_TYPES,
                "action_types": ACTION_TYPES,
            },
        )
    except Exception as e:
        logger.error(f"Error loading triggers page: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/triggers/logs/{log_id}")
async def get_log_details(
    request: Request,
    log_id: str,
    authorized=Depends(Permissions.MANAGE_GROUPS),
):
    """Get details of a specific trigger execution log"""
    try:
        db = request.app.state.mongodb_client.API
        log = await db.trigger_logs.find_one({"_id": ObjectId(log_id)})
        
        if not log:
            raise HTTPException(status_code=404, detail="Log not found")
            
        # Convert ObjectId to string for JSON serialization
        log["_id"] = str(log["_id"])
        return log
    except Exception as e:
        logger.error(f"Error fetching log details: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

def validate_python_script(script_content: str) -> bool:
    """
    Validate that the Python script contains a properly defined run(data) function.
    Returns True if valid, raises ValueError with description if invalid.
    """
    try:
        # Parse the script into an AST
        tree = ast.parse(script_content)
        
        # Look for the run function
        run_func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'run':
                run_func = node
                break
        
        if not run_func:
            raise ValueError("Script must contain a 'run' function")
        
        # Check function arguments
        args = run_func.args
        if len(args.args) != 1:
            raise ValueError("run function must accept exactly one parameter")
        
        if args.args[0].arg != 'data':
            raise ValueError("run function parameter must be named 'data'")
        
        return True
        
    except SyntaxError as e:
        raise ValueError(f"Script contains syntax errors: {str(e)}")
    except Exception as e:
        raise ValueError(f"Error validating script: {str(e)}")

@app.post("/triggers/add")
async def add_trigger(
    request: Request,
    authorized=Depends(Permissions.MANAGE_GROUPS),
    name: str = Form(...),
    description: str = Form(None),
    event_type: str = Form(...),
    action_type: str = Form(...),
    is_active: bool = Form(True),
    config: str = Form(...),
    admin=Depends(get_current_admin),
):
    """Add a new trigger"""
    try:
        config_data = json.loads(config)
        
        # Validate script if action type is python_script
        if action_type == 'python_script':
            script_content = config_data.get('script')
            if not script_content:
                raise ValueError("Script content is required for python_script action type")
            
            # Validate the script
            validate_python_script(script_content)
        
        trigger = {
            "name": name,
            "description": description,
            "event_type": event_type,
            "action_type": action_type,
            "is_active": is_active,
            "config": config_data,
            "created_at": datetime.utcnow(),
            "created_by": admin.id
        }
        
        result = await request.app.state.mongodb_client.API.triggers.insert_one(trigger)
        
        return RedirectResponse(
            url="/admin/triggers",
            status_code=HTTP_303_SEE_OTHER
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error adding trigger: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/triggers/{trigger_id}/edit")
async def edit_trigger(
    request: Request,
    trigger_id: str,
    authorized=Depends(Permissions.MANAGE_GROUPS),
    name: str = Form(...),
    description: str = Form(None),
    event_type: str = Form(...),
    action_type: str = Form(...),
    is_active: bool = Form(True),
    config: str = Form(...),
    admin=Depends(get_current_admin),
):
    """Edit an existing trigger"""
    try:
        config_data = json.loads(config)
        
        # Validate script if action type is python_script
        if action_type == 'python_script':
            script_content = config_data.get('script')
            if not script_content:
                raise ValueError("Script content is required for python_script action type")
            
            # Validate the script
            validate_python_script(script_content)
        
        update_data = {
            "name": name,
            "description": description,
            "event_type": event_type,
            "action_type": action_type,
            "is_active": is_active,
            "config": config_data,
            "updated_at": datetime.utcnow(),
            "updated_by": admin.id
        }
        
        result = await request.app.state.mongodb_client.API.triggers.update_one(
            {"_id": ObjectId(trigger_id)},
            {"$set": update_data}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Trigger not found")
        
        return RedirectResponse(
            url="/admin/triggers",
            status_code=HTTP_303_SEE_OTHER
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating trigger: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/triggers/{trigger_id}/delete")
async def delete_trigger(
    request: Request,
    trigger_id: str,
    authorized=Depends(Permissions.MANAGE_GROUPS),
    admin=Depends(get_current_admin),
):
    try:
        # Get MongoDB client and collection
        client = app.state.mongodb_client
        db = client.API
        triggers_collection = db.triggers

        # Delete trigger
        result = await triggers_collection.delete_one({"_id": ObjectId(trigger_id)})

        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Trigger not found")

        return RedirectResponse(url="/admin/triggers", status_code=HTTP_303_SEE_OTHER)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error deleting trigger: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete trigger")

@app.post("/triggers/{trigger_id}/test")
async def test_trigger(
    request: Request,
    trigger_id: str,
    authorized=Depends(Permissions.MANAGE_GROUPS),
    admin=Depends(get_current_admin),
):
    try:
        # Get MongoDB client and collection
        client = app.state.mongodb_client
        db = client.API
        triggers_collection = db.triggers

        # Get trigger
        trigger = await triggers_collection.find_one({"_id": ObjectId(trigger_id)})
        if not trigger:
            raise HTTPException(status_code=404, detail="Trigger not found")

        # Create test data based on event type
        test_data = create_test_data(trigger["event_type"])
        
        # Execute trigger action
        result = await execute_trigger_action(trigger, test_data)

        # Log the test execution
        await log_trigger_execution(
            app.state.mongodb_client,
            trigger_id=trigger_id,
            event_data=test_data,
            success=True,
            response=result,
            is_test=True
        )

        return JSONResponse({"status": "success", "message": "Trigger tested successfully", "result": result})
    except Exception as e:
        logger.error(f"Error testing trigger: {str(e)}")
        # Log the failed test
        await log_trigger_execution(
            app.state.mongodb_client,
            trigger_id=trigger_id,
            event_data={},
            success=False,
            response=str(e),
            is_test=True
        )
        raise HTTPException(status_code=500, detail=f"Failed to test trigger: {str(e)}")

async def log_trigger_execution(
    mongodb_client,
    trigger_id: str,
    event_data: dict,
    success: bool,
    response: any,
    is_test: bool = False
):
    """Log trigger execution details to MongoDB"""
    try:
        mongodb_client = app.state.mongodb_client
        db = mongodb_client.API
        log_entry = {
            "trigger_id": trigger_id,
            "timestamp": datetime.utcnow(),
            "event_data": event_data,
            "success": success,
            "response": response,
            "is_test": is_test
        }
        await db.trigger_logs.insert_one(log_entry)
    except Exception as e:
        logger.error(f"Error logging trigger execution: {str(e)}")

def create_test_data(event_type: str) -> dict:
    """Create sample test data based on event type"""
    test_data = {
        "user_created": {
            "user_id": "test_user_123",
            "email": "test@example.com",
            "name": "Test User"
        },
        "property_created": {
            "property_id": "test_prop_123",
            "address": "123 Test St",
            "price": 500000
        },
        "payment_made": {
            "payment_id": "test_payment_123",
            "amount": 1000,
            "status": "completed"
        },
        "showing_request_created": {
            "request_id": "test_showing_123",
            "property_id": "test_prop_123",
            "user_id": "test_user_123",
            "datetime": "2024-03-20T10:00:00Z"
        },
        "service_requested": {
            "service_id": "test_service_123",
            "type": "home_inspection",
            "property_id": "test_prop_123"
        }
    }
    return test_data.get(event_type, {})

@app.get("/service-leads")
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

@app.get("/service-leads/{lead_id}")
async def get_lead_details(
    request: Request,
    lead_id: str,
    authorized=Depends(Permissions.MANAGE_USERS),
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

@app.post("/service-leads/{lead_id}/update")
async def update_lead_status(
    request: Request,
    lead_id: str,
    authorized=Depends(Permissions.MANAGE_USERS),
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

@app.post("/service-leads/export")
async def export_leads(
    request: Request,
    authorized=Depends(Permissions.MANAGE_USERS),
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

@app.get("/document-editor")
async def document_editor(
    request: Request,
    resources=Depends(get_resources),
    admin=Depends(get_current_admin),
    page: int = Query(1, ge=1),
    per_page: int = 10,
    search: str = Query(None),
    question_id: str = Query(None),
):
    try:
        # Initialize MongoDB client
        mongodb_client = request.app.state.mongodb_client
        db = mongodb_client.API

        # Build the query
        query = {}
        if search:
            # Search in name or _id
            query["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"_id": ObjectId(search)} if len(search) == 24 and all(c in '0123456789abcdefABCDEF' for c in search) else {"_id": None}
            ]
        
        # If question_id is provided, find documents that have this question
        if question_id:
            doc_ids = await db.doc_questions_answers.distinct(
                "document_id",
                {"_id": question_id}
            )
            if doc_ids:
                query["_id"] = {"$in": doc_ids}
            else:
                # If no documents found with this question, return empty
                query["_id"] = None

        # Get total count
        total_documents = await db.documents.count_documents(query)
        
        # Get paginated documents
        skip = (page - 1) * per_page
        cursor = db.documents.find(query).skip(skip).limit(per_page)
        documents = await cursor.to_list(length=per_page)

        # Calculate pagination info
        total_pages = (total_documents + per_page - 1) // per_page
        has_next = page < total_pages
        has_prev = page > 1

        return templates.TemplateResponse(
            "document-editor.html",
            {
                "request": request,
                "resources": resources,
                "admin": admin,
                "documents": documents,
                "page": page,
                "total_pages": total_pages,
                "has_next": has_next,
                "has_prev": has_prev,
                "total_documents": total_documents,
                "search": search or "",
                "question_id": question_id or "",
            },
        )
    except Exception as e:
        print(f"Error in document_editor: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/document-editor/{document_id}")
async def document_editor_detail(
    request: Request,
    document_id: str,
    resources=Depends(get_resources),
    admin=Depends(get_current_admin),
):
    try:
        # Initialize MongoDB client
        mongodb_client = request.app.state.mongodb_client
        db = mongodb_client.API

        # Get document details
        document = await db.documents.find_one({"_id": ObjectId(document_id)})
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        # Get document questions
        questions = await db.doc_questions_answers.find(
            {"document_id": document_id}
        ).to_list(length=None)

        return templates.TemplateResponse(
            "document-editor-detail.html",
            {
                "request": request,
                "resources": resources,
                "admin": admin,
                "document": document,
                "questions": questions,
            },
        )
    except Exception as e:
        print(f"Error in document_editor_detail: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/document-editor/{document_id}/update-question")
async def update_document_question(
    request: Request,
    document_id: str,
    admin=Depends(get_current_admin),
):
    try:
        # Initialize MongoDB client
        mongodb_client = request.app.state.mongodb_client
        db = mongodb_client.API

        # Get the request body
        data = await request.json()
        questions = data.get('questions', [])
        
        if not questions:
            raise HTTPException(status_code=400, detail="No questions provided")

        # Track results
        results = {
            "success": [],
            "failed": []
        }

        # Process each question
        for question_data in questions:
            try:
                question_id = question_data.get('question_id')
                if not question_id:
                    results["failed"].append({
                        "error": "Missing question_id",
                        "data": question_data
                    })
                    continue

                # Prepare update data
                update_data = {
                    "question": question_data['question'],
                    "type": question_data['type'],
                    "page": question_data['page'],
                    "original_question_text": question_data['original_question_text'],
                    "placeholder": question_data.get('placeholder', ''),
                    "link": question_data.get('link', ''),
                    "tooltip": question_data.get('tooltip', ''),
                    "answer_locations": question_data.get('answer_locations', [])
                }

                # Update the question
                result = await db.doc_questions_answers.update_one(
                    {"_id": ObjectId(question_id), "document_id": document_id},
                    {"$set": update_data}
                )

                if result.modified_count > 0:
                    results["success"].append(question_id)
                else:
                    results["failed"].append({
                        "question_id": question_id,
                        "error": "Question not found or no changes made",
                        "data": question_data
                    })

            except Exception as e:
                results["failed"].append({
                    "question_id": question_data.get('question_id'),
                    "error": str(e),
                    "data": question_data
                })

        # If no questions were successfully updated, return an error
        if not results["success"] and results["failed"]:
            raise HTTPException(
                status_code=400, 
                detail={
                    "message": "All updates failed",
                    "results": results
                }
            )

        return {
            "status": "success",
            "results": results
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"Error in update_document_question: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


