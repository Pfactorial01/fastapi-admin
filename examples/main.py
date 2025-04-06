import os
from contextlib import asynccontextmanager
import logging
from motor.motor_asyncio import AsyncIOMotorClient

import redis.asyncio as redis
import uvicorn
from fastapi import FastAPI, Request
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
from examples.scheduler import scheduler  # Import the scheduler
from fastapi_admin.app import app as admin_app
from fastapi_admin.exceptions import (
    forbidden_error_exception,
    not_found_error_exception,
    server_error_exception,
    unauthorized_error_exception,
)

# Configure logging with more detailed format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def create_app():
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Initialize MongoDB client
        mongodb_client = AsyncIOMotorClient(settings.MONGODB_URL)
        
        # Verify MongoDB connection
        try:
            await mongodb_client.admin.command('ping')
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

        # Initialize scheduler
        await scheduler.init_app(app)
        
        yield
        
        # Cleanup
        await scheduler.shutdown()
        await Tortoise.close_connections()
        if hasattr(app.state, 'mongodb_client'):
            mongodb_client.close()

    # Create the FastAPI app with the lifespan context manager
    app = FastAPI(lifespan=lifespan)
    
    @app.on_event("startup")
    async def startup_event():
        pass
        
    @app.on_event("shutdown")
    async def shutdown_event():
        pass

    # Mount static files
    app.mount(
        "/static",
        StaticFiles(directory=os.path.join(BASE_DIR, "static")),
        name="static",
    )

    @app.get("/")
    async def index(request: Request):
        try:
            # Try to get the current admin
            admin = request.state.admin
            if not admin:
                return RedirectResponse(url="/admin/login")
            return RedirectResponse(url="/admin")
        except Exception:
            return RedirectResponse(url="/admin/login")

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
    uvicorn.run("examples.main:app_", host="0.0.0.0", port=8080, reload=True)
