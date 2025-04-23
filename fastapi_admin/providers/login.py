import typing
import uuid
from typing import Type

import redis.asyncio as redis
from fastapi import Depends, Form
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER, HTTP_401_UNAUTHORIZED
from tortoise import signals

from fastapi_admin import constants
from fastapi_admin.depends import get_current_admin, get_redis, get_resources
from fastapi_admin.i18n import _
from fastapi_admin.models import AbstractAdmin
from fastapi_admin.providers import Provider
from fastapi_admin.template import templates
from fastapi_admin.utils import check_password, hash_password
from examples.models import Groups, Permission

if typing.TYPE_CHECKING:
    from fastapi_admin.app import FastAPIAdmin


class UsernamePasswordProvider(Provider):
    name = "login_provider"

    access_token = "access_token"

    def __init__(
        self,
        admin_model: Type[AbstractAdmin],
        login_path="/login",
        logout_path="/logout",
        template="providers/login/login.html",
        login_title="Login to your account",
        login_logo_url: str = None,
    ):
        self.login_path = login_path
        self.logout_path = logout_path
        self.template = template
        self.admin_model = admin_model
        self.login_title = login_title
        self.login_logo_url = login_logo_url

    async def login_view(
        self,
        request: Request,
    ):
        return templates.TemplateResponse(
            self.template,
            context={
                "request": request,
                "login_logo_url": self.login_logo_url,
                "login_title": self.login_title,
            },
        )

    async def register(self, app: "FastAPIAdmin"):
        await super(UsernamePasswordProvider, self).register(app)
        login_path = self.login_path
        app.get(login_path)(self.login_view)
        app.post(login_path)(self.login)
        app.get(self.logout_path)(self.logout)
        app.add_middleware(BaseHTTPMiddleware, dispatch=self.authenticate)
        app.get("/init")(self.init_view)
        app.post("/init")(self.init)
        app.get("/password")(self.password_view)
        app.post("/password")(self.password)
        signals.pre_save(self.admin_model)(self.pre_save_admin)

    async def pre_save_admin(self, _, instance: AbstractAdmin, using_db, update_fields):
        if instance.pk:
            db_obj = await instance.get(pk=instance.pk)
            if db_obj.password != instance.password:
                instance.password = hash_password(instance.password)
        else:
            instance.password = hash_password(instance.password)

    async def login(self, request: Request, redis: redis.Redis = Depends(get_redis)):
        form = await request.form()
        username = form.get("username")
        password = form.get("password")
        remember_me = form.get("remember_me")
        admin = await self.admin_model.get_or_none(username=username)
        if not admin or not check_password(password, admin.password):
            return templates.TemplateResponse(
                self.template,
                status_code=HTTP_401_UNAUTHORIZED,
                context={"request": request, "error": _("login_failed")},
            )
        response = RedirectResponse(url=request.app.admin_path, status_code=HTTP_303_SEE_OTHER)
        if remember_me == "on":
            expire = 3600 * 24 * 30
            response.set_cookie("remember_me", "on")
        else:
            expire = 3600
            response.delete_cookie("remember_me")
        token = uuid.uuid4().hex
        response.set_cookie(
            self.access_token,
            token,
            expires=expire,
            path=request.app.admin_path,
            httponly=True,
        )
        await redis.set(constants.LOGIN_USER.format(token=token), admin.pk, ex=expire)
        return response

    async def logout(self, request: Request):
        response = self.redirect_login(request)
        response.delete_cookie(self.access_token, path=request.app.admin_path)
        token = request.cookies.get(self.access_token)
        await request.app.redis.delete(constants.LOGIN_USER.format(token=token))
        return response

    async def authenticate(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ):
        redis = request.app.redis  # type:ignore
        token = request.cookies.get(self.access_token)
        path = request.scope["path"]
        admin = None
        if token:
            token_key = constants.LOGIN_USER.format(token=token)
            admin_id = await redis.get(token_key)
            admin = await self.admin_model.get_or_none(pk=admin_id)
        request.state.admin = admin

        if path == self.login_path and admin:
            return RedirectResponse(url=request.app.admin_path, status_code=HTTP_303_SEE_OTHER)

        response = await call_next(request)
        return response

    async def create_user(self, username: str, password: str, **kwargs):
        return await self.admin_model.create(username=username, password=password, **kwargs)

    async def init_view(self, request: Request):
        exists = await self.admin_model.all().limit(1).exists()
        if exists:
            return self.redirect_login(request)
        return templates.TemplateResponse("init.html", context={"request": request})

    async def init(
        self,
        request: Request,
    ):
        exists = await self.admin_model.all().limit(1).exists()
        if exists:
            return self.redirect_login(request)
        form = await request.form()
        password = form.get("password")
        confirm_password = form.get("confirm_password")
        username = form.get("username")
        if password != confirm_password:
            return templates.TemplateResponse(
                "init.html",
                context={"request": request, "error": _("confirm_password_different")},
            )

        permissions = [
            # Settings Management
            {
                "name": "View Settings",
                "code": "view_settings",
                "route": "/admin/settings",
                "type": "view",
                "category": "Settings",
                "description": "Permission to view system settings"
            },
            {
                "name": "Manage Users Settings",
                "code": "manage_users_settings",
                "route": "/admin/settings/users",
                "type": "manage",
                "category": "Settings",
                "description": "Permission to add, edit, and delete users"
            },
            {
                "name": "Manage Groups Settings",
                "code": "manage_groups_settings",
                "route": "/admin/settings/groups",
                "type": "manage",
                "category": "Settings",
                "description": "Permission to add, edit, and delete groups"
            },
            {
                "name": "Manage Permissions Settings",
                "code": "manage_permissions_settings",
                "route": "/admin/settings/permissions",
                "type": "manage",
                "category": "Settings",
                "description": "Permission to add, edit, and delete permissions"
            },

            # Document Editor
            {
                "name": "View Document Editor",
                "code": "view_document_editor",
                "route": "/admin/document-editor",
                "type": "view",
                "category": "Documents",
                "description": "Permission to view document editor"
            },
            {
                "name": "Manage Document Editor",
                "code": "manage_document_editor",
                "route": "/admin/document-editor",
                "type": "manage",
                "category": "Documents",
                "description": "Permission to edit documents and update questions"
            },

            # Service Leads
            {
                "name": "View Service Leads",
                "code": "view_service_leads",
                "route": "/admin/service-leads",
                "type": "view",
                "category": "Service Leads",
                "description": "Permission to view service leads list and details"
            },
            {
                "name": "Manage Service Leads",
                "code": "manage_service_leads",
                "route": "/admin/service-leads",
                "type": "manage",
                "category": "Service Leads",
                "description": "Permission to update and export service leads"
            },

            # Triggers
            {
                "name": "View Triggers",
                "code": "view_triggers",
                "route": "/admin/triggers",
                "type": "view",
                "category": "Triggers",
                "description": "Permission to view triggers and logs"
            },
            {
                "name": "Manage Triggers",
                "code": "manage_triggers",
                "route": "/admin/triggers",
                "type": "manage",
                "category": "Triggers",
                "description": "Permission to add, edit, delete and test triggers"
            },

            # Showing Requests
            {
                "name": "View Showing Requests",
                "code": "view_showing_requests",
                "route": "/admin/showing-requests",
                "type": "view",
                "category": "Showings",
                "description": "Permission to view showing requests"
            },
            {
                "name": "Manage Showing Requests",
                "code": "manage_showing_requests",
                "route": "/admin/showing-requests",
                "type": "manage",
                "category": "Showings",
                "description": "Permission to process and export showing requests"
            },

            # Search Functionality
            {
                "name": "Search Agents",
                "code": "search_agents",
                "route": "/admin/agents/search",
                "type": "view",
                "category": "Search",
                "description": "Permission to search agents"
            },
            {
                "name": "Search Properties",
                "code": "search_properties",
                "route": "/admin/properties/search",
                "type": "view",
                "category": "Search",
                "description": "Permission to search properties"
            },
            {
                "name": "Search Users",
                "code": "search_users",
                "route": "/admin/users/search",
                "type": "view",
                "category": "Search",
                "description": "Permission to search users"
            },

            # App Version Management
            {
                "name": "View App Versions",
                "code": "view_app_versions",
                "route": "/admin/app-version-management",
                "type": "view",
                "category": "App Versions",
                "description": "Permission to view app versions"
            },
            {
                "name": "Manage App Versions",
                "code": "manage_app_versions",
                "route": "/admin/app-version-management",
                "type": "manage",
                "category": "App Versions",
                "description": "Permission to add, update and delete app versions"
            },

            # User Management
            {
                "name": "View User Management",
                "code": "view_user_management",
                "route": "/admin/user-management",
                "type": "view",
                "category": "Users",
                "description": "Permission to view user profiles and management"
            },
            {
                "name": "Manage User Information",
                "code": "manage_user_information",
                "route": "/admin/user-management",
                "type": "manage",
                "category": "Users",
                "description": "Permission to update user information and verification status"
            },

            # ID Verification
            {
                "name": "View ID Verifications",
                "code": "view_id_verifications",
                "route": "/admin/id-verification",
                "type": "view",
                "category": "Verification",
                "description": "Permission to view ID verification requests"
            },
            {
                "name": "Manage ID Verifications",
                "code": "manage_id_verifications",
                "route": "/admin/id-verification",
                "type": "manage",
                "category": "Verification",
                "description": "Permission to update ID verifications"
            },

            # Notifications
            {
                "name": "View Notifications",
                "code": "view_notifications",
                "route": "/admin/notifications",
                "type": "view",
                "category": "Notifications",
                "description": "Permission to view notifications"
            },
            {
                "name": "Manage Notifications",
                "code": "manage_notifications",
                "route": "/admin/notifications",
                "type": "manage",
                "category": "Notifications",
                "description": "Permission to send notifications"
            },

            # Messages
            {
                "name": "View Messages",
                "code": "view_messages",
                "route": "/admin/messages",
                "type": "view",
                "category": "Messages",
                "description": "Permission to view messages"
            },
            {
                "name": "Manage Messages",
                "code": "manage_messages",
                "route": "/admin/messages",
                "type": "manage",
                "category": "Messages",
                "description": "Permission to send messages"
            },

            # Property Verification
            {
                "name": "View Property Verifications",
                "code": "view_property_verifications",
                "route": "/admin/property-verification",
                "type": "view",
                "category": "Properties",
                "description": "Permission to view property verification requests and details"
            },
            {
                "name": "Manage Property Verifications",
                "code": "manage_property_verifications",
                "route": "/admin/property-verification",
                "type": "manage",
                "category": "Properties",
                "description": "Permission to process property verifications"
            },

            # Documents
            {
                "name": "View Documents",
                "code": "view_documents",
                "route": "/admin/documents",
                "type": "view",
                "category": "Documents",
                "description": "Permission to view documents and user documents"
            },

            # Home Dashboard
            {
                "name": "View Dashboard",
                "code": "view_dashboard",
                "route": "/admin",
                "type": "view",
                "category": "Dashboard",
                "description": "Permission to view admin dashboard"
            }
        ]

        # Create super_user group with all permissions
        super_group = await Groups.create(
            name="Super User",
            description="Super user group with all permissions",
            is_active=True,
        )
        
        # Create all permissions and add to super_group
        for perm_data in permissions: 
            permission = await Permission.create(
                name=perm_data["name"],
                code=perm_data["code"],
                route=perm_data["route"],
                type=perm_data["type"],
                category=perm_data["category"],
                description=perm_data["description"],
                is_active=True
            )
            await super_group.permissions.add(permission)

        # Create first admin user and assign super_user group
        admin = await self.create_user(username, password)
        admin.group = super_group
        await admin.save()

        return self.redirect_login(request)

    def redirect_login(self, request: Request):
        return RedirectResponse(
            url=request.app.admin_path + self.login_path, status_code=HTTP_303_SEE_OTHER
        )

    async def password_view(
        self,
        request: Request,
        resources=Depends(get_resources),
    ):
        return templates.TemplateResponse(
            "providers/login/password.html",
            context={
                "request": request,
                "resources": resources,
            },
        )

    async def password(
        self,
        request: Request,
        old_password: str = Form(...),
        new_password: str = Form(...),
        re_new_password: str = Form(...),
        admin: AbstractAdmin = Depends(get_current_admin),
        resources=Depends(get_resources),
    ):
        error = None
        if not check_password(old_password, admin.password):
            error = _("old_password_error")
        elif new_password != re_new_password:
            error = _("new_password_different")
        if error:
            return templates.TemplateResponse(
                "password.html",
                context={"request": request, "resources": resources, "error": error},
            )
        admin.password = new_password
        await admin.save(update_fields=["password"])
        return await self.logout(request)

