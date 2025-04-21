from enum import Enum
from functools import wraps
from typing import List, Optional, Callable

from fastapi import Depends, HTTPException
from starlette.requests import Request
from starlette.status import HTTP_403_FORBIDDEN

from fastapi_admin.depends import get_current_admin

class PermissionDependency:
    def __init__(self, permissions: List[str]):
        self.permissions = permissions

    async def __call__(self, request: Request, admin=Depends(get_current_admin)):
        # Fetch the group relationship
        await admin.fetch_related('group')
        
        if not admin.group:
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail="No group assigned to user"
            )

        # Super users can access everything
        if admin.group.name == "super_user":
            return True

        # Check if the user's group has the required permissions
        user_permissions = {
            "can_view_users": admin.group.can_view_users,
            "can_manage_users": admin.group.can_manage_users,
            "can_chat_users": admin.group.can_chat_users,
            "can_view_properties": admin.group.can_view_properties,
            "can_manage_properties": admin.group.can_manage_properties,
            "can_manage_showing_requests": admin.group.can_manage_showing_requests,
            "can_manage_groups": admin.group.can_manage_groups,
            "can_view_audit_logs": admin.group.can_view_audit_logs,
            "can_manage_notifications": admin.group.can_manage_notifications,
        }

        for permission in self.permissions:
            if not user_permissions.get(permission, False):
                raise HTTPException(
                    status_code=HTTP_403_FORBIDDEN,
                    detail=f"Missing required permission: {permission}"
                )
        
        return True

# Predefined permission sets for different endpoints
class Permissions:
    # User Management
    VIEW_USERS = PermissionDependency(["can_view_users"])
    MANAGE_USERS = PermissionDependency(["can_manage_users"])
    CHAT_WITH_USERS = PermissionDependency(["can_chat_users"])

    # Property Management
    VIEW_PROPERTIES = PermissionDependency(["can_view_properties"])
    MANAGE_PROPERTIES = PermissionDependency(["can_manage_properties"])
    
    # Showing Requests
    MANAGE_SHOWING_REQUESTS = PermissionDependency(["can_manage_showing_requests"])
    
    # Groups and Settings
    MANAGE_GROUPS = PermissionDependency(["can_manage_groups"])
    VIEW_AUDIT_LOGS = PermissionDependency(["can_view_audit_logs"])
    MANAGE_NOTIFICATIONS = PermissionDependency(["can_manage_notifications"])

    # Combined permissions for complex operations
    USER_VERIFICATION = PermissionDependency(["can_view_users", "can_manage_users"])
    PROPERTY_VERIFICATION = PermissionDependency(["can_view_properties", "can_manage_properties"])
    
    # Group-specific permission sets
    CUSTOMER_SERVICE_BASE = PermissionDependency([
        "can_view_users",
        "can_chat_users",
        "can_view_properties",
        "can_manage_showing_requests"
    ])
    
    VERIFICATION_TEAM_BASE = PermissionDependency([
        "can_view_users",
        "can_manage_users",
        "can_view_properties",
        "can_manage_properties"
    ])

    # Chat-specific permissions
    CHAT_ACCESS = PermissionDependency([
        "can_view_users",
        "can_chat_users"
    ])

# Helper function to combine multiple permission dependencies
def combine_permissions(permissions: List[PermissionDependency]) -> Callable:
    """Combine multiple permission dependencies into a single dependency"""
    async def combined_dependency(request: Request, admin=Depends(get_current_admin)):
        for permission in permissions:
            await permission(request, admin)
        return True
    return combined_dependency

# Example usage of combined permissions:
FULL_USER_MANAGEMENT = combine_permissions([
    Permissions.VIEW_USERS,
    Permissions.MANAGE_USERS,
    Permissions.CHAT_WITH_USERS
])

FULL_PROPERTY_MANAGEMENT = combine_permissions([
    Permissions.VIEW_PROPERTIES,
    Permissions.MANAGE_PROPERTIES
]) 