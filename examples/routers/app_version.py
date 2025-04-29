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
from examples.permissions import PermissionDependency

# Configure logging
logger = logging.getLogger(__name__)

@app.get("/app-version-management")
async def app_version_management(
    request: Request,
    resources=Depends(get_resources),
    authorize=Depends(PermissionDependency(["view_app_versions"])),
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
    authorize=Depends(PermissionDependency(["manage_app_versions"])),
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
    authorize=Depends(PermissionDependency(["manage_app_versions"])),
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
    authorize=Depends(PermissionDependency(["manage_app_versions"])),
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

