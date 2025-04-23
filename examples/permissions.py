from enum import Enum
from functools import wraps
from typing import List, Optional, Callable

from fastapi import Depends, HTTPException
from starlette.requests import Request
from starlette.status import HTTP_403_FORBIDDEN

from fastapi_admin.depends import get_current_admin
from examples.models import Permission, PermissionType

class PermissionDependency:
    def __init__(self, permission_codes: List[str]):
        self.permission_codes = permission_codes

    async def __call__(self, request: Request, admin=Depends(get_current_admin)):
        # Fetch the group relationship
        await admin.fetch_related('group')
        
        if not admin.group:
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail="No group assigned to user"
            )

        # Super users can access everything
        if admin.group.name == "Super User":
            return True

        # Check each required permission
        for permission_code in self.permission_codes:
            has_permission = await admin.has_permission(permission_code)
            if not has_permission:
                raise HTTPException(
                    status_code=HTTP_403_FORBIDDEN,
                    detail=f"Missing required permission: {permission_code}"
                )
        
        return True

# Predefined permission sets for different endpoints
class Permissions:
    # Settings Management
    VIEW_SETTINGS = PermissionDependency(["view_settings"])
    MANAGE_USERS_SETTINGS = PermissionDependency(["manage_users_settings"])
    MANAGE_GROUPS_SETTINGS = PermissionDependency(["manage_groups_settings"])
    MANAGE_PERMISSIONS_SETTINGS = PermissionDependency(["manage_permissions_settings"])

    # Document Management
    VIEW_DOCUMENT_EDITOR = PermissionDependency(["view_document_editor"])
    MANAGE_DOCUMENT_EDITOR = PermissionDependency(["manage_document_editor"])
    VIEW_DOCUMENTS = PermissionDependency(["view_documents"])

    # Service Leads
    VIEW_SERVICE_LEADS = PermissionDependency(["view_service_leads"])
    MANAGE_SERVICE_LEADS = PermissionDependency(["manage_service_leads"])

    # Triggers
    VIEW_TRIGGERS = PermissionDependency(["view_triggers"])
    MANAGE_TRIGGERS = PermissionDependency(["manage_triggers"])

    # Showing Requests
    VIEW_SHOWING_REQUESTS = PermissionDependency(["view_showing_requests"])
    MANAGE_SHOWING_REQUESTS = PermissionDependency(["manage_showing_requests"])

    # Search Functionality
    SEARCH_AGENTS = PermissionDependency(["search_agents"])
    SEARCH_PROPERTIES = PermissionDependency(["search_properties"])
    SEARCH_USERS = PermissionDependency(["search_users"])

    # App Version Management
    VIEW_APP_VERSIONS = PermissionDependency(["view_app_versions"])
    MANAGE_APP_VERSIONS = PermissionDependency(["manage_app_versions"])

    # User Management
    VIEW_USER_MANAGEMENT = PermissionDependency(["view_user_management"])
    MANAGE_USER_INFORMATION = PermissionDependency(["manage_user_information"])

    # ID Verification
    VIEW_ID_VERIFICATIONS = PermissionDependency(["view_id_verifications"])
    MANAGE_ID_VERIFICATIONS = PermissionDependency(["manage_id_verifications"])

    # Notifications
    VIEW_NOTIFICATIONS = PermissionDependency(["view_notifications"])
    MANAGE_NOTIFICATIONS = PermissionDependency(["manage_notifications"])

    # Messages
    VIEW_MESSAGES = PermissionDependency(["view_messages"])
    MANAGE_MESSAGES = PermissionDependency(["manage_messages"])

    # Property Verification
    VIEW_PROPERTY_VERIFICATIONS = PermissionDependency(["view_property_verifications"])
    MANAGE_PROPERTY_VERIFICATIONS = PermissionDependency(["manage_property_verifications"])

    # Dashboard
    VIEW_DASHBOARD = PermissionDependency(["view_dashboard"])
