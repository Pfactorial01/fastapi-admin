from fastapi import Depends, HTTPException, Query, Form
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER, HTTP_404_NOT_FOUND, HTTP_400_BAD_REQUEST
import logging
from bson import ObjectId
from datetime import datetime
import asyncio

from examples.services.fcm_service import FCMService
from fastapi_admin.app import app
from fastapi_admin.depends import get_resources, get_current_admin
from fastapi_admin.template import templates
from examples.permissions import PermissionDependency


# Configure logging
logger = logging.getLogger(__name__)

@app.post("/property_verification", dependencies=[Depends(PermissionDependency(["manage_property_verifications"]))])
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

@app.get("/property-verification", dependencies=[Depends(PermissionDependency(["view_property_verifications"]))])
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

@app.get("/property-verification/{verification_id}", dependencies=[Depends(PermissionDependency(["view_property_verifications"]))])
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

