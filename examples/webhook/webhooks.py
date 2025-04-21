from fastapi import APIRouter, Depends, HTTPException, Request
from ..auth.webhook_auth import verify_webhook_api_key
from ..triggers.executor import execute_trigger_action
import logging
from datetime import datetime
from bson import ObjectId

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

async def execute_triggers(request: Request, event_type: str, event_data: dict):
    """Execute all active triggers for a given event type."""
    try:
        # Get MongoDB client and collection
        client = request.app.state.mongodb_client
        db = client.API
        triggers_collection = db.triggers

        # Find all active triggers for this event type
        cursor = triggers_collection.find({
            "event_type": event_type,
            "is_active": True
        })

        results = []
        async for trigger in cursor:
            try:
                # Execute the trigger
                result = await execute_trigger_action(trigger, event_data)
                
                # Log the execution
                await db.trigger_logs.insert_one({
                    "trigger_id": str(trigger["_id"]),
                    "event_type": event_type,
                    "event_data": event_data,
                    "timestamp": datetime.utcnow(),
                    "success": True,
                    "response": result
                })

                results.append({
                    "trigger_id": str(trigger["_id"]),
                    "status": "success",
                    "result": result
                })

            except Exception as e:
                logger.error(f"Error executing trigger {trigger['_id']}: {str(e)}")
                # Log the failed execution
                await db.trigger_logs.insert_one({
                    "trigger_id": str(trigger["_id"]),
                    "event_type": event_type,
                    "event_data": event_data,
                    "timestamp": datetime.utcnow(),
                    "success": False,
                    "error": str(e)
                })

                results.append({
                    "trigger_id": str(trigger["_id"]),
                    "status": "error",
                    "error": str(e)
                })

        return results

    except Exception as e:
        logger.error(f"Error processing triggers for event {event_type}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing triggers: {str(e)}")

@router.post("/user-created")
async def user_created_webhook(
    request: Request,
    _=Depends(verify_webhook_api_key)
):
    try:
        payload = await request.json()
        logger.info(f"Processing user creation webhook: {payload}")
        
        # Execute triggers for this event
        trigger_results = await execute_triggers(request, "user_created", payload)
        
        return {
            "status": "success",
            "message": "User creation webhook processed",
            "trigger_results": trigger_results
        }
    except Exception as e:
        logger.error(f"Error processing user creation webhook: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/property-created")
async def property_created_webhook(
    request: Request,
    _=Depends(verify_webhook_api_key)
):
    try:
        payload = await request.json()
        logger.info(f"Processing property creation webhook: {payload}")
        
        # Execute triggers for this event
        trigger_results = await execute_triggers(request, "property_created", payload)
        
        return {
            "status": "success",
            "message": "Property creation webhook processed",
            "trigger_results": trigger_results
        }
    except Exception as e:
        logger.error(f"Error processing property creation webhook: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/showing-request-created")
async def showing_request_created_webhook(
    request: Request,
    _=Depends(verify_webhook_api_key)
):
    try:
        payload = await request.json()
        logger.info(f"Processing showing request webhook: {payload}")
        
        # Execute triggers for this event
        trigger_results = await execute_triggers(request, "showing_request_created", payload)
        
        return {
            "status": "success",
            "message": "Showing request creation webhook processed",
            "trigger_results": trigger_results
        }
    except Exception as e:
        logger.error(f"Error processing showing request webhook: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/payment-made")
async def payment_made_webhook(
    request: Request,
    _=Depends(verify_webhook_api_key)
):
    try:
        payload = await request.json()
        logger.info(f"Processing payment webhook: {payload}")
        
        # Execute triggers for this event
        trigger_results = await execute_triggers(request, "payment_made", payload)
        
        return {
            "status": "success",
            "message": "Payment webhook processed",
            "trigger_results": trigger_results
        }
    except Exception as e:
        logger.error(f"Error processing payment webhook: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/service-requested")
async def service_requested_webhook(
    request: Request,
    _=Depends(verify_webhook_api_key)
):
    try:
        payload = await request.json()
        logger.info(f"Processing service request webhook: {payload}")
        
        # Execute triggers for this event
        trigger_results = await execute_triggers(request, "service_requested", payload)
        
        return {
            "status": "success",
            "message": "Service request webhook processed",
            "trigger_results": trigger_results
        }
    except Exception as e:
        logger.error(f"Error processing service request webhook: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e)) 