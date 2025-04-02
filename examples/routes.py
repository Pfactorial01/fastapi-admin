from fastapi import Depends, HTTPException, Query, Form
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

from examples.models import Config
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
                print("property_info: ", property_info)
            
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
                message["message"] = process_message_markdown(message.get("message", ""))
            
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
    message: str = Form(...),
    admin=Depends(get_current_admin),
):
    try:
        print("listing_id: ", listing_id)
        # Redirect back to the messages page
        redirect_url = f"/admin/messages?user_id={user_id}{f'&listing_id={listing_id}' if listing_id != 'None' else ''}"
                # Make the API call to send the message
        api_url = "https://api.airebrokers.com/project-api/api1/user/customer-service-reply"
        headers = {
            "Authorization": "Bearer 9xplm2q5v4tsd93wykbzfac8no",
            "Content-Type": "application/json"
        }
        payload = {
            "message": message,
            "user_id": user_id
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(api_url, json=payload, headers=headers)
            response.raise_for_status()  # Raise an exception for bad status codes

            
        return RedirectResponse(url=redirect_url, status_code=HTTP_303_SEE_OTHER)
        
    except Exception as e:
        logger.error(f"Error sending message: {str(e)}")
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to send message")
