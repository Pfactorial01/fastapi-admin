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


@app.get("/messages")
async def messages(
    request: Request,
    resources=Depends(get_resources),
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

@app.post("/messages/send", dependencies=[Depends(Permissions.MANAGE_MESSAGES)])
async def send_message(
    request: Request,
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
