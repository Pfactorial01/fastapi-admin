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
import stripe
from motor.motor_asyncio import AsyncIOMotorClient
from tortoise.expressions import Q

from examples.models import Admin, Config, Groups, Permission, Subscription
from examples.services.fcm_service import FCMService
from examples.triggers.executor import execute_trigger_action
from fastapi_admin.app import app
from fastapi_admin.depends import get_resources, get_current_admin
from fastapi_admin.template import templates
from examples.permissions import Permissions

# Configure Stripe
stripe.api_key = settings.STRIPE_SECRET_KEY

# Configure logging
logger = logging.getLogger(__name__)

@app.get("/revenue-management")
async def revenue_management(
    request: Request,
    resources=Depends(get_resources),
    admin=Depends(get_current_admin),
    page: int = Query(1, ge=1),
    per_page: int = 10,
    status: Optional[str] = None,
    package_type: Optional[str] = None,
    search: Optional[str] = None,
    date_range: Optional[str] = None,
):
    """Render the revenue management dashboard"""
    try:
        client = request.app.state.mongodb_client
        db = client.API
        
        # Get packages from MongoDB with sorting
        packages_cursor = db.packages.find().sort("price", 1)
        packages = []
        async for package in packages_cursor:
            # Convert ObjectId to string
            package['_id'] = str(package['_id'])
            packages.append(package)
        
        # Build Tortoise ORM query
        query = Q()
        if status:
            query &= Q(status=status)
        if package_type:
            query &= Q(package_type=package_type)
        if search:
            query &= Q(user_id__icontains=search)
        if date_range:
            try:
                start_date, end_date = date_range.split(" - ")
                start_dt = datetime.strptime(start_date.strip(), "%m/%d/%Y")
                # For same-day queries, we want to include all entries on that day
                # So start from midnight of start date
                start_dt = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                
                # If dates are the same, end_dt should be end of the day
                if start_date.strip() == end_date.strip():
                    end_dt = start_dt + timedelta(days=1)
                else:
                    end_dt = datetime.strptime(end_date.strip(), "%m/%d/%Y")
                    # For different dates, also ensure we include the full end date
                    end_dt = (end_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                
                query &= Q(created_at__gte=start_dt) & Q(created_at__lt=end_dt)
                logger.info(f"Date range query: from {start_dt} to {end_dt}")
            except ValueError as e:
                logger.error(f"Error parsing date range '{date_range}': {str(e)}")
                # Continue without date filtering if format is invalid
                pass
            
        # Get total count for pagination
        total_docs = await Subscription.filter(query).count()
        total_pages = (total_docs + per_page - 1) // per_page
        
        # Calculate pagination values
        start_page = max(1, page - 2)
        end_page = min(total_pages, page + 2)
        page_range = list(range(start_page, end_page + 1))
        start_showing = (page - 1) * per_page + 1
        end_showing = min(page * per_page, total_docs)
        
        # Get paginated subscriptions
        subscriptions = await Subscription.filter(query).offset((page - 1) * per_page).limit(per_page).all()
        
        # Calculate dashboard metrics
        now = datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        active_subs = await Subscription.filter(status="active").count()
        failed_payments = await Subscription.filter(status="failed").count()
        
        # Calculate monthly revenue (from active subscriptions created this month)
        monthly_revenue = await Subscription.filter(
            status="active",
            created_at__gte=month_start
        ).all()
        monthly_revenue_amount = sum(sub.amount for sub in monthly_revenue)
        
        # Calculate total revenue from all active subscriptions
        total_revenue = await Subscription.filter(status="active").all()
        total_revenue_amount = sum(sub.amount for sub in total_revenue)
        
        # Calculate revenue by package
        active_subs_by_package = await Subscription.filter(status="active").all()
        revenue_by_package = {}
        for sub in active_subs_by_package:
            if sub.package_type not in revenue_by_package:
                revenue_by_package[sub.package_type] = 0
            revenue_by_package[sub.package_type] += sub.amount
        
        context = {
            "request": request,
            "resources": resources,
            "admin": admin,
            "packages": packages,
            "subscriptions": subscriptions,
            "revenue_by_package": revenue_by_package,
            "monthly_revenue": monthly_revenue_amount,
            "total_revenue": total_revenue_amount,
            "active_subscriptions": active_subs,
            "failed_payments": failed_payments,
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
            "revenue_management.html",
            context=context
        )
        
    except Exception as e:
        logger.error(f"Error in revenue management route: {str(e)}", exc_info=True)
        return templates.TemplateResponse(
            "revenue_management.html",
            context={
                "request": request,
                "resources": resources,
                "admin": admin,
                "error": f"Failed to load revenue management data: {str(e)}",
                "packages": [],
                "subscriptions": [],
                "revenue_by_package": {},
                "monthly_revenue": 0,
                "total_revenue": 0,
                "active_subscriptions": 0,
                "failed_payments": 0,
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
            }
        )

@app.post("/revenue-management/add-package")
async def add_package(
    request: Request,
    package_name: str = Form(...),
    price: float = Form(...),
    description: str = Form(...),
    information: List[str] = Form(...),
    admin=Depends(get_current_admin),
):
    """Add a new package"""
    try:
        client = request.app.state.mongodb_client
        db = client.API

        # Check if package already exists
        existing_package = await db.packages.find_one({"package_name": package_name})
        if existing_package:
            raise HTTPException(status_code=400, detail="Package with this name already exists")

        # Create new package
        package = {
            "package_name": package_name,
            "price": price,
            "description": description,
            "information": information,
            "created_at": datetime.now(),
            "created_by": admin.username,
            "updated_at": datetime.now(),
            "updated_by": admin.username
        }

        await db.packages.insert_one(package)

        # Create audit log
        audit_entry = {
            "action": "package_created",
            "package_name": package_name,
            "admin_username": admin.username,
            "timestamp": datetime.now(),
            "details": package
        }
        await db.audit_log.insert_one(audit_entry)

        return RedirectResponse(
            url="/revenue-management",
            status_code=HTTP_303_SEE_OTHER
        )

    except Exception as e:
        logger.error(f"Error adding package: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/revenue-management/update-package/{package_name}")
async def update_package(
    request: Request,
    package_name: str,
    new_package_name: str = Form(...),
    price: float = Form(...),
    description: str = Form(...),
    information: List[str] = Form(...),
    admin=Depends(get_current_admin),
):
    """Update an existing package"""
    try:
        client = request.app.state.mongodb_client
        db = client.API

        # Check if package exists
        existing_package = await db.packages.find_one({"package_name": package_name})
        if not existing_package:
            raise HTTPException(status_code=404, detail="Package not found")

        # If package name is being changed, check if new name is available
        if package_name != new_package_name:
            name_check = await db.packages.find_one({"package_name": new_package_name})
            if name_check:
                raise HTTPException(status_code=400, detail="Package with new name already exists")

        # Update package
        update_data = {
            "package_name": new_package_name,
            "price": price,
            "description": description,
            "information": information,
            "updated_at": datetime.now(),
            "updated_by": admin.username
        }

        await db.packages.update_one(
            {"package_name": package_name},
            {"$set": update_data}
        )

        # Create audit log
        audit_entry = {
            "action": "package_updated",
            "package_name": package_name,
            "new_package_name": new_package_name,
            "admin_username": admin.username,
            "timestamp": datetime.now(),
            "details": {
                "before": existing_package,
                "after": update_data
            }
        }
        await db.audit_log.insert_one(audit_entry)

        return RedirectResponse(
            url="/revenue-management",
            status_code=HTTP_303_SEE_OTHER
        )

    except Exception as e:
        logger.error(f"Error updating package: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/revenue-management/delete-package/{package_name}")
async def delete_package(
    request: Request,
    package_name: str,
    admin=Depends(get_current_admin),
):
    """Delete a package"""
    try:
        client = request.app.state.mongodb_client
        db = client.API

        # Check if package exists
        package = await db.packages.find_one({"package_name": package_name})
        if not package:
            raise HTTPException(status_code=404, detail="Package not found")

        # Check if package is in use
        active_subscriptions = await Subscription.filter(
            package_type=package_name,
            status="active"
        ).count()
        
        if active_subscriptions > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete package: {active_subscriptions} active subscriptions are using this package"
            )

        # Delete package
        await db.packages.delete_one({"package_name": package_name})

        # Create audit log
        audit_entry = {
            "action": "package_deleted",
            "package_name": package_name,
            "admin_username": admin.username,
            "timestamp": datetime.now(),
            "details": package
        }
        await db.audit_log.insert_one(audit_entry)

        return RedirectResponse(
            url="/revenue-management",
            status_code=HTTP_303_SEE_OTHER
        )

    except Exception as e:
        logger.error(f"Error deleting package: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/revenue-management/refund")
async def process_refund(
    request: Request,
    charge_id: str = Form(...),
    property_id: str = Form(...),
    amount: float = Form(...),
    reason: str = Form(...),
    admin=Depends(get_current_admin),
):
    """Process a refund for a charge"""
    try:
        client = request.app.state.mongodb_client
        db = client.API
        # Check if subscription is already refunded
        subscription = await Subscription.get_or_none(charge_id=charge_id)
        if not subscription:
            raise HTTPException(
                status_code=404, 
                detail="Subscription not found"
            )
            
        if subscription.status == "refunded":
            raise HTTPException(
                status_code=400,
                detail="This charge has already been refunded"
            )
        # Convert amount to cents for Stripe
        amount_cents = int(amount * 100)

        # Process refund through Stripe
        try:
            refund = stripe.Refund.create(
                charge=charge_id,
                amount=amount_cents,
                reason=reason
            )
        except stripe.error.StripeError as e:
            logger.error(f"Stripe refund error: {str(e)}")
            raise HTTPException(
                status_code=400,
                detail=f"Failed to process refund: {str(e)}"
            )
        await Subscription.filter(charge_id=charge_id).update(status="refunded")

        # downgrade property immediately
        await db.properties.update_one(
            {"_id": ObjectId(property_id)},
            {"$set": {"package_type": "basic", "auto_renew": False}}
        )

        # Create audit log entry
        audit_entry = {
            "action": "refund_processed",
            "charge_id": charge_id,
            "refund_id": refund.id,
            "amount": amount,
            "reason": reason,
            "admin_username": admin.username,
            "timestamp": datetime.now(),
            "details": {
                "stripe_response": {
                    "id": refund.id,
                    "status": refund.status,
                    "amount": refund.amount,
                },
            }
        }
        await db.audit_log.insert_one(audit_entry)

        # Return success response
        return JSONResponse({
            "success": True,
            "message": "Refund processed successfully",
            "refund_id": refund.id
        })

    except HTTPException as e:
        # Re-raise HTTP exceptions
        raise

    except Exception as e:
        logger.error(f"Error processing refund: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process refund: {str(e)}"
        )

@app.post("/revenue-management/cancel/{subscription_id}")
async def cancel_subscription(
    request: Request,
    subscription_id: str,
    admin=Depends(get_current_admin),
):
    """Cancel a subscription"""
    try:
        client = request.app.state.mongodb_client
        db = client.API

        # Get subscription
        subscription = await Subscription.get_or_none(id=subscription_id)
        if not subscription:
            raise HTTPException(
                status_code=404,
                detail="Subscription not found"
            )

        if subscription.status in ['refunded', 'canceled']:
            raise HTTPException(
                status_code=400,
                detail="Subscription is already canceled or refunded"
            )

        # Update subscription status
        await Subscription.filter(id=subscription_id).update(status="canceled")

        # Turn off auto-renewal for the property
        await db.properties.update_one(
            {"_id": ObjectId(subscription.property_id)},
            {"$set": {"auto_renew": False}}
        )

        # Create audit log entry
        audit_entry = {
            "action": "subscription_canceled",
            "subscription_id": subscription_id,
            "admin_username": admin.username,
            "timestamp": datetime.now(),
            "details": {
                "property_id": subscription.property_id,
                "previous_status": subscription.status,
                "new_status": "canceled"
            }
        }
        await db.audit_log.insert_one(audit_entry)

        return JSONResponse({
            "success": True,
            "message": "Subscription canceled successfully"
        })

    except HTTPException as e:
        raise

    except Exception as e:
        logger.error(f"Error canceling subscription: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to cancel subscription: {str(e)}"
        )
    
    