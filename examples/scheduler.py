import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
import logging
from datetime import datetime
from typing import Optional, List
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from examples.services.fcm_service import FCMService
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Test log to verify module loading
logger.info("Scheduler module loaded")

class SchedulerManager:
    def __init__(self):
        self.scheduler = None
        self._app: Optional[FastAPI] = None
        self._startup_complete = False
        self._initialized = False
        logging.getLogger('apscheduler').setLevel(logging.WARNING)  # Reduce APScheduler logs

    async def start(self):
        """Start the scheduler if it's not already running."""
        if not self._initialized:
            logger.error("Cannot start scheduler: FastAPI app not initialized")
            return

        try:
            # Create scheduler with AsyncIO executor
            self.scheduler = AsyncIOScheduler()
            
            if not self.scheduler.running:
                self.scheduler.start()
                self._startup_complete = True
            
            # Add scheduled job - note that we pass the async function directly
            self.scheduler.add_job(
                func=self.process_pending_notifications,  # Pass async function directly
                trigger=CronTrigger(minute="*"),
                id="process_notifications",
                name="Process pending notifications",
                replace_existing=True,
                misfire_grace_time=None,
                max_instances=1
            )
            
            # # Run initial job
            # asyncio.create_task(self.process_pending_notifications())
                
        except Exception as e:
            logger.error(f"Error starting scheduler: {str(e)}", exc_info=True)
            raise

    async def shutdown(self):
        """Shutdown the scheduler if it's running."""
        try:
            if self.scheduler and self.scheduler.running:
                self.scheduler.shutdown()
                self._startup_complete = False
        except Exception as e:
            logger.error(f"Error shutting down scheduler: {str(e)}", exc_info=True)
            raise

    async def init_app(self, app: FastAPI):
        """Initialize the scheduler with the FastAPI app."""
        if self._initialized:
            return

        self._app = app
        self._initialized = True
        await self.start()

    async def get_target_users(self, db, notification: dict) -> List[str]:
        """Get target users based on notification type."""
        target_users = []
        if notification["target_type"] == "all":
            async for user in db.users.find({"device_token": {"$exists": True}}):
                target_users.append(user["uuid"])
        elif notification["target_type"] == "individual":
            target_users = notification["target_users"]
        # elif notification["target_type"] == "radius":
        #     location = notification["target_location"]["coordinates"]
        #     radius_meters = notification["target_radius"] * 1000
        #     async for user in db.users.find({
        #         "location": {
        #             "$nearSphere": {
        #                 "$geometry": {
        #                     "type": "Point",
        #                     "coordinates": location
        #                 },
        #                 "$maxDistance": radius_meters
        #             }
        #         }
        #     }):
        #         target_users.append(user["uuid"])
        
        return target_users

    async def process_pending_notifications(self):
        """Process pending notifications job."""
        if not self._app:
            return

        client = None
        try:
            # Create a new MongoDB client for this job execution
            mongodb_url = os.getenv('MONGODB_URL')
            client = AsyncIOMotorClient(mongodb_url)
            db = client.API
            
            query = {
                "status": "pending",
                "scheduled_for": {"$lte": datetime.now(timezone.utc)}
            }
            
            pending_count = await db.notifications.count_documents(query)
            if pending_count == 0:
                return
                    
            async for notification in db.notifications.find(query):
                try:
                    await db.notifications.update_one(
                        {"_id": notification["_id"]},
                        {"$set": {"status": "processing"}}
                    )
                    
                    target_users = await self.get_target_users(db, notification)
                    tokens = []
                    async for user in db.users.find({"uuid": {"$in": target_users}}):
                        if user.get("device_token"):
                            tokens.append(user["device_token"])
                    
                    # Prepare notification data
                    notification_data = {}
                    if notification.get("link"):
                        notification_data["link"] = notification["link"]
                    
                    fcm_service = FCMService()
                    # Create task instead of awaiting
                    asyncio.create_task(fcm_service.send_notification(
                        tokens=tokens,
                        title=notification["title"],
                        body=notification["message"],
                        data=notification_data if notification_data else None,
                        notification_id=str(notification["_id"]),
                        db_client=client
                    ))
                    
                    # Create user notifications in bulk
                    user_notifications = [
                        {
                            "user_id": user_id,
                            "notification_id": notification["_id"],
                            "title": notification["title"],
                            "message": notification["message"],
                            "link": notification.get("link"),
                            "created_at": datetime.utcnow(),
                            "is_read": False,
                            "type": "system",  # Indicates this is a system notification
                            "status": "sent",
                            "metadata": {
                                "target_type": notification["target_type"],
                                "scheduled": True,
                                "scheduled_for": notification.get("scheduled_for")
                            }
                        }
                        for user_id in target_users
                    ]

                    if user_notifications:
                        # Use ordered=False for better performance
                        await db.user_notifications.insert_many(user_notifications, ordered=False)
                    
                    # Mark as processing since we're not waiting for the result
                    await db.notifications.update_one(
                        {"_id": notification["_id"]},
                        {
                            "$set": {
                                "status": "processing",
                                "processed_at": datetime.utcnow()
                            }
                        }
                    )
                    
                except Exception as e:
                    logger.error(f"Error processing notification {notification['_id']}: {e}")
                    await db.notifications.update_one(
                        {"_id": notification["_id"]},
                        {
                            "$set": {
                                "status": "failed",
                                "error": str(e),
                                "processed_at": datetime.utcnow()
                            }
                        }
                    )
            
        except Exception as e:
            logger.error(f"Error in notification processing: {e}", exc_info=True)
        finally:
            if client:
                client.close()

    def add_job(self, func, trigger, **kwargs):
        """Add a job to the scheduler."""
        return self.scheduler.add_job(func, trigger, **kwargs)

# Create a global instance
scheduler = SchedulerManager() 