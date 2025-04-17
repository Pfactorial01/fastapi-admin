from fastapi import Depends, File, HTTPException, Query, Form, UploadFile
import httpx
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER, HTTP_404_NOT_FOUND, HTTP_400_BAD_REQUEST, HTTP_500_INTERNAL_SERVER_ERROR
import logging
from examples import settings
from bson import ObjectId
import markdown
from markdown.extensions import fenced_code, tables, nl2br
from datetime import datetime
from typing import List
import os
import asyncio

from examples.models import Config
from examples.services.fcm_service import FCMService
from fastapi_admin.app import app
from fastapi_admin.depends import get_resources, get_current_admin
from fastapi_admin.template import templates

# Configure logging
logger = logging.getLogger(__name__)

# Initialize markdown converter with extensions
md = markdown.Markdown(extensions=['fenced_code', 'tables', 'nl2br'])

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
    admin=Depends(get_current_admin),
    page: int = Query(1, ge=1),
    per_page: int = 10,
    user_id: str = Query(None),
    listing_id: str = Query(None),  # Add listing_id parameter
    load_older: bool = Query(False),  # Flag to load older messages
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
    user_id: str = Form(...),
    listing_id: str = Form(None),
    message: str = Form(None),  # Make message optional
    file: UploadFile = File(None),
    admin=Depends(get_current_admin),
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

