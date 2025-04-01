from fastapi import Depends, HTTPException, Query
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER, HTTP_404_NOT_FOUND, HTTP_400_BAD_REQUEST
import logging
from examples import settings
from bson import ObjectId

from examples.models import Config
from fastapi_admin.app import app
from fastapi_admin.depends import get_resources, get_current_admin
from fastapi_admin.template import templates

# Configure logging
logger = logging.getLogger(__name__)

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
        # Get MongoDB client from admin app state
        logger.info("Accessing MongoDB client from admin app state")
        if not hasattr(app.state, 'mongodb_client'):
            logger.error("MongoDB client not initialized in admin app state")
            raise Exception("MongoDB client not initialized")
            
        client = app.state.mongodb_client
        logger.info(f"MongoDB client status: {client}")
        
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
        # Get MongoDB client from admin app state
        logger.info("Accessing MongoDB client from admin app state")
        if not hasattr(app.state, 'mongodb_client'):
            logger.error("MongoDB client not initialized in admin app state")
            raise Exception("MongoDB client not initialized")
            
        client = app.state.mongodb_client
        logger.info(f"MongoDB client status: {client}")
        
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
