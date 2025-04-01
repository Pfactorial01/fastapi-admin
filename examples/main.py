import os
from contextlib import asynccontextmanager
import logging
from motor.motor_asyncio import AsyncIOMotorClient

import redis.asyncio as redis
import uvicorn
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import RedirectResponse
from starlette.staticfiles import StaticFiles
from starlette.status import (
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_500_INTERNAL_SERVER_ERROR,
)
from tortoise.contrib.fastapi import register_tortoise
from tortoise.exceptions import DBConnectionError
from tortoise import Tortoise

from examples import settings
from examples.constants import BASE_DIR
from examples.models import Admin, Category, Product, Config
from examples.providers import LoginProvider
from fastapi_admin.app import app as admin_app
from fastapi_admin.exceptions import (
    forbidden_error_exception,
    not_found_error_exception,
    server_error_exception,
    unauthorized_error_exception,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_app():
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Initialize MongoDB client
        logger.info("Initializing MongoDB client...")
        mongodb_client = AsyncIOMotorClient(settings.MONGODB_URL)
        
        # Verify MongoDB connection
        try:
            await mongodb_client.admin.command('ping')
            logger.info("Successfully connected to MongoDB")
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise
        
        # Store MongoDB client in both apps' state
        app.state.mongodb_client = mongodb_client
        admin_app.state.mongodb_client = mongodb_client
        
        # Initialize Tortoise
        await Tortoise.init(
            db_url=settings.DATABASE_URL,
            modules={'models': ['examples.models']}
        )
        
        # Generate schemas
        await Tortoise.generate_schemas()
        
        # Initialize Redis and configure admin app
        r = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            encoding="utf8",
        )
        
        # Configure admin app
        await admin_app.configure(
            logo_url="https://preview.tabler.io/static/logo-white.svg",
            template_folders=[os.path.join(BASE_DIR, "templates")],
            favicon_url="https://raw.githubusercontent.com/fastapi-admin/fastapi-admin/dev/images/favicon.png",
            providers=[
                LoginProvider(
                    login_logo_url="https://preview.tabler.io/static/logo.svg",
                    admin_model=Admin,
                )
            ],
            redis=r,
        )
        
        yield
        
        # Cleanup
        await Tortoise.close_connections()
        if hasattr(app.state, 'mongodb_client'):
            logger.info("Closing MongoDB connection...")
            app.state.mongodb_client.close()

    app = FastAPI(lifespan=lifespan)
    app.mount(
        "/static",
        StaticFiles(directory=os.path.join(BASE_DIR, "static")),
        name="static",
    )

    @app.get("/")
    async def index():
        return RedirectResponse(url="/admin")

    admin_app.add_exception_handler(HTTP_500_INTERNAL_SERVER_ERROR, server_error_exception)
    admin_app.add_exception_handler(HTTP_404_NOT_FOUND, not_found_error_exception)
    admin_app.add_exception_handler(HTTP_403_FORBIDDEN, forbidden_error_exception)
    admin_app.add_exception_handler(HTTP_401_UNAUTHORIZED, unauthorized_error_exception)

    # Mount admin app
    app.mount("/admin", admin_app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
    # Register Tortoise with FastAPI (but don't generate schemas as we do it in lifespan)
    register_tortoise(
        app,
        config={
            "connections": {"default": settings.DATABASE_URL},
            "apps": {
                "models": {
                    "models": ["examples.models"],
                    "default_connection": "default",
                }
            },
        },
        generate_schemas=False,  # We generate schemas in lifespan
    )

    return app


app_ = create_app()

if __name__ == "__main__":
    uvicorn.run("main:app_", reload=True)
