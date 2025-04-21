from fastapi import HTTPException, Request
from fastapi.security import APIKeyHeader
from typing import Optional
import os
import logging
from dotenv import load_dotenv
from fastapi import Depends

logger = logging.getLogger(__name__)

# Define the header name for the API key
WEBHOOK_API_KEY_HEADER = "X-Webhook-API-Key"

# Create the API key header security scheme
api_key_header = APIKeyHeader(name=WEBHOOK_API_KEY_HEADER, auto_error=False)

load_dotenv()

async def verify_webhook_api_key(request: Request, api_key: Optional[str] = Depends(api_key_header)) -> None:
    """
    Verify the webhook API key from the request header.
    Raises HTTPException if the API key is invalid or missing.
    """
    expected_api_key = os.getenv("WEBHOOK_API_KEY")
    
    if not expected_api_key:
        logger.error("WEBHOOK_API_KEY environment variable is not set")
        raise HTTPException(
            status_code=500,
            detail="Server configuration error: Webhook API key not configured"
        )

    if not api_key:
        logger.warning(f"Missing API key in request header {WEBHOOK_API_KEY_HEADER}")
        raise HTTPException(
            status_code=401,
            detail=f"Missing API key in {WEBHOOK_API_KEY_HEADER} header"
        )

    if api_key != expected_api_key:
        logger.warning("Invalid webhook API key provided")
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )

    logger.debug("Webhook API key verified successfully")

    # Add rate limiting logic here if needed
    return None 