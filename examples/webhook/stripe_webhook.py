from fastapi import APIRouter, Depends, HTTPException, Request

from examples import settings
from ..models import Subscription
import stripe
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/stripe", tags=["stripe-webhooks"])

# Configure Stripe
stripe.api_key = settings.STRIPE_SECRET_KEY
endpoint_secret = settings.STRIPE_WEBHOOK_SECRET

async def handle_charge_succeeded(event_data: dict):
    """Handle successful charge"""
    try:
        charge = event_data['object']
        data = charge['metadata']
        transaction_id = data.get('transaction_id')
        if not transaction_id:
            return {
                "status": "ignored",
                "message": "No transaction_id in charge metadata"
            }
        await Subscription.create(
            charge_id=charge['id'],
            transaction_id=transaction_id,
            status="active",
            package_type=data.get('package_type'),
            amount=data.get('payment_amount'),
            billing_cycle="monthly",
            next_billing_date=datetime.now() + timedelta(days=30),
            last_billing_date=datetime.fromtimestamp(charge['created']),
            user_id=data.get('seller_id'),
            property_id=data.get('property_id'),
        )
        return {"status": "success"}
        
    except Exception as e:
        logger.error(f"Error handling charge.succeeded: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

async def handle_charge_failed(event_data: dict):
    """Handle failed charge"""
    try:
        charge = event_data['object']
        data = charge['metadata']
        transaction_id = data.get('transaction_id')
        if not transaction_id:
            return {
                "status": "ignored",
                "message": "No transaction_id in charge metadata"
            }
        await Subscription.create(
            charge_id=charge['id'],
            transaction_id=transaction_id,
            status="failed",
            package_type=data.get('package_type'),
            amount=data.get('payment_amount'),
            billing_cycle="monthly",
            next_billing_date=datetime.now() + timedelta(days=30),
            last_billing_date=datetime.fromtimestamp(charge['created']),
            user_id=data.get('seller_id'),
            property_id=data.get('property_id'),
        )
        return {"status": "success"}
        
    except Exception as e:
        logger.error(f"Error handling charge.failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# async def handle_charge_refunded(event_data: dict):
#     """Handle refunded charge"""
#     try:
#         charge = event_data['object']
#         data = charge['metadata']
#         transaction_id = data.get('transaction_id')
#         if not transaction_id:
#             return {
#                 "status": "ignored",
#                 "message": "No transaction_id in charge metadata"
#             }
#         await Subscription.create(
#             charge_id=charge['id'],
#             transaction_id=transaction_id,
#             status="refunded",
#             package_type=data.get('package_type'),
#             amount=data.get('payment_amount'),
#             billing_cycle="monthly",
#         )
#         return {"status": "success"}
        
#     except Exception as e:
#         logger.error(f"Error handling charge.refunded: {str(e)}")
#         raise HTTPException(status_code=500, detail=str(e))


async def verify_stripe_webhook(
        request: Request,
    ) -> dict:
    """Verify Stripe webhook signature and return event data"""
    try:
        payload = await request.body()
        sig_header = request.headers.get('stripe-signature')
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, endpoint_secret
            )
            return event
        except Exception as e:
            raise HTTPException(status_code=401, detail=str(e))
            
    except Exception as e:
        logger.error(f"Error verifying webhook: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))

@router.post("/")
async def stripe_webhook(
    request: Request,
):
    try:
        # Verify webhook signature and get event data
        event = await verify_stripe_webhook(request)
        # Map event types to their handlers
        event_handlers = {
            'charge.succeeded': handle_charge_succeeded,
            'charge.failed': handle_charge_failed,
            # 'charge.refunded': handle_charge_refunded,
        }
        
        # Get the appropriate handler for this event type
        handler = event_handlers.get(event.type)
        if not handler:
            return {
                "status": "ignored",
                "message": f"Unhandled event type: {event.type}"
            }
            
        # Handle the event
        result = await handler(event.data)
        
        # Log the successful webhook processing
        logger.info(f"Successfully processed {event.type} webhook")
        
        return {
            "status": "success",
            "event_type": event.type,
            "result": result
        }
        
    except Exception as e:
        logger.error(f"Error processing Stripe webhook: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
