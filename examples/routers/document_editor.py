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

@app.get("/document-editor", dependencies=[Depends(Permissions.VIEW_DOCUMENT_EDITOR)])
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
            doc_ids = await db.doc_questions_answers.find_one(
                {"_id": ObjectId(question_id)},
            )
            if doc_ids:
                query["_id"] = ObjectId(doc_ids.get('document_id'))
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

@app.get("/document-editor/{document_id}", dependencies=[Depends(Permissions.VIEW_DOCUMENT_EDITOR)])
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

@app.post("/document-editor/{document_id}/update-question", dependencies=[Depends(Permissions.MANAGE_DOCUMENT_EDITOR)])
async def update_document_question(
    request: Request,
    document_id: str,
    question_id: str = Form(...),
    question_text: str = Form(...),
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
                    "video_link": question_data.get('video_link', ''),
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

