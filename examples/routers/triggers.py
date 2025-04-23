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


# Configure logging
logger = logging.getLogger(__name__)

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

@app.get("/triggers")
async def triggers(
    request: Request,
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

