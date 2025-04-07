import firebase_admin
from firebase_admin import credentials, messaging, initialize_app, get_app, delete_app
import logging
import json
import os
from typing import List, Dict, Optional, Any, Tuple
import base64
import asyncio
import functools
import requests

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FCMService:
    _instance = None
    _app = None
    _initialized = False
    _project_id = None
    _fcm_send_url = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(FCMService, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not FCMService._initialized:
            self._initialize_fcm()
            FCMService._initialized = True

    def _verify_credentials(self, creds_dict: Dict[str, Any]) -> Tuple[bool, str]:
        """Verify the Firebase credentials and project configuration."""
        try:
            project_id = creds_dict.get('project_id')
            if not project_id:
                return False, "Project ID not found in credentials"

            # Verify required fields
            required_keys = {'type', 'project_id', 'private_key_id', 'private_key', 'client_email'}
            missing_keys = required_keys - set(creds_dict.keys())
            if missing_keys:
                return False, f"Missing required keys: {', '.join(missing_keys)}"

            # Verify credential type
            if creds_dict.get('type') != 'service_account':
                return False, "Invalid credential type. Must be 'service_account'"

            # Set FCM URL
            self._fcm_send_url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
            
            # Test if project exists by making a test request
            test_message = messaging.Message(
                topic="test_topic",
                notification=messaging.Notification(
                    title="Test",
                    body="Test"
                )
            )
            
            try:
                messaging.send(test_message, dry_run=True, app=self._app)
                return True, "Credentials verified successfully"
            except messaging.ApiCallError as e:
                if 'UNREGISTERED' in str(e):
                    # This is actually a good sign - it means we can reach FCM but the topic doesn't exist
                    return True, "Credentials verified successfully"
                return False, f"FCM API error: {str(e)}"
            
        except Exception as e:
            return False, f"Verification error: {str(e)}"

    def _initialize_fcm(self) -> None:
        """Initialize Firebase Admin SDK using credentials from environment variables."""
        try:
            # Clean up any existing Firebase apps
            self._cleanup_existing_apps()

            # Get credentials from environment variable
            encoded_creds = os.getenv('GOOGLE_SERVICE_ACCOUNT_KEY')
            if not encoded_creds:
                raise ValueError("GOOGLE_SERVICE_ACCOUNT_KEY environment variable is not set")

            # Decode base64 credentials
            try:
                decoded_creds_bytes = base64.b64decode(encoded_creds)
                decoded_creds_str = decoded_creds_bytes.decode('utf-8')
                creds_dict = json.loads(decoded_creds_str)
            except Exception as e:
                raise ValueError(f"Failed to decode credentials: {str(e)}")

            # Create credentials object
            cred = credentials.Certificate(creds_dict)
            
            # Initialize app first
            FCMService._app = initialize_app(cred)
            
            # Now verify the credentials and configuration
            is_valid, message = self._verify_credentials(creds_dict)
            if not is_valid:
                self._cleanup_existing_apps()
                raise ValueError(f"Invalid Firebase configuration: {message}")

            # Store project ID
            self._project_id = creds_dict['project_id']
            logger.info(f"Firebase Admin SDK initialized for project: {self._project_id}")
            logger.info(f"FCM URL: {self._fcm_send_url}")

        except Exception as e:
            logger.error(f"Failed to initialize Firebase Admin SDK: {str(e)}")
            raise

    def _cleanup_existing_apps(self):
        """Clean up any existing Firebase apps."""
        try:
            apps = firebase_admin._apps.copy()
            for name, app in apps.items():
                delete_app(app)
        except Exception as e:
            logger.warning(f"Error cleaning up existing Firebase apps: {str(e)}")

    async def verify_token(self, token: str) -> Tuple[bool, str]:
        """Verify if a token is valid."""
        try:
            # Try to send a dry run message to the token
            message = messaging.Message(
                token=token,
                notification=messaging.Notification(
                    title="Token Verification",
                    body="This is a dry run to verify the token"
                )
            )
            messaging.send(message, dry_run=True, app=self._app)
            return True, "Token is valid"
        except messaging.ApiCallError as e:
            error_msg = str(e)
            if 'registration-token-not-registered' in error_msg.lower():
                return False, "Token is not registered"
            elif 'invalid-argument' in error_msg.lower():
                return False, "Token is invalid"
            else:
                return False, f"Token verification failed: {error_msg}"
        except Exception as e:
            return False, f"Token verification failed: {str(e)}"

    async def send_notification(
        self,
        tokens: List[str],
        title: str,
        body: str,
        data: Optional[Dict[str, str]] = None,
        image_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send notifications to multiple devices using FCM."""
        if not self._app:
            logger.error("FCM not properly initialized")
            return {"success": 0, "failure": len(tokens), "error": "FCM not initialized"}

        if not tokens:
            return {"success": 0, "failure": 0, "invalid_tokens": []}

        # Process data payload
        processed_data = {str(k): str(v) for k, v in (data or {}).items()}

        success_count = 0
        failure_count = 0
        invalid_tokens = []
        
        try:
            # Create base message configuration
            android_config = messaging.AndroidConfig(
                priority='high',
                notification=messaging.AndroidNotification(
                    icon='notification_icon',
                    color='#4CAF50',
                    sound='default'
                )
            )
            
            apns_config = messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        sound='default',
                        badge=1
                    )
                )
            )

            # Send messages individually
            loop = asyncio.get_running_loop()
            
            for token in tokens:
                try:
                    # Create individual message
                    message = messaging.Message(
                        notification=messaging.Notification(
                            title=title,
                            body=body,
                            # image=image_url
                        ),
                        data=processed_data,
                        token=token,  # Single token instead of tokens list
                        android=android_config,
                        apns=apns_config
                    )

                    # Send message
                    send_message = functools.partial(messaging.send, message=message, app=self._app)
                    await loop.run_in_executor(None, send_message)
                    success_count += 1
                    
                except messaging.UnregisteredError:
                    failure_count += 1
                    invalid_tokens.append(token)
                # except messaging.ApiCallError as e:
                #     logger.error(f"Failed to send to token {token[-6:]}: {str(e)}")
                #     failure_count += 1
                except Exception as e:
                    logger.error(f"Unexpected error for token {token[-6:]}: {str(e)}")
                    failure_count += 1

            return {
                "success": success_count,
                "failure": failure_count,
                "invalid_tokens": invalid_tokens
            }

        except Exception as e:
            logger.error(f"Error in send_notification: {str(e)}")
            return {
                "success": 0,
                "failure": len(tokens),
                "error": str(e),
                "invalid_tokens": []
            }

# Create a global instance
fcm_service = FCMService()