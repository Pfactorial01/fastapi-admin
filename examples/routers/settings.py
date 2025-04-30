from fastapi import Depends, HTTPException, Query, Form
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
import logging
from typing import List

from examples.models import Admin, Groups, Permission
from fastapi_admin.app import app
from fastapi_admin.depends import get_resources, get_current_admin
from fastapi_admin.template import templates
from examples.permissions import PermissionDependency

# Configure logging
logger = logging.getLogger(__name__)

@app.get("/settings", dependencies=[Depends(PermissionDependency(["view_settings"]))])
async def settings(
    request: Request,
    resources=Depends(get_resources),
    admin=Depends(get_current_admin),
    tab: str = Query("users", regex="^(users|groups|permissions)$"),
    page: int = Query(1, ge=1),
    per_page: int = 10,
):
    try:
        total_docs = 0
        items = []
        
        if tab == "users":
            # Get total users count
            total_docs = await Admin.all().count()
            
            # Get paginated users with their groups
            items = await Admin.all().offset((page - 1) * per_page).limit(per_page).prefetch_related('group')
            
            # Format user data
            items = [{
                "id": user.pk,
                "username": user.username,
                "email": user.email,
                "last_login": user.last_login,
                "created_at": user.created_at,
                "group": {
                    "id": user.group.pk,
                    "name": user.group.name
                } if user.group else None
            } for user in items]
            
        elif tab == "permissions":
            # Get total permissions count
            total_docs = await Permission.all().count()
            
            # Get paginated permissions
            items = await Permission.all().offset((page - 1) * per_page).limit(per_page)
            
            # Format permission data
            items = [{
                "id": perm.pk,
                "name": perm.name,
                "code": perm.code,
                "route": perm.route,
                "type": perm.type,
                "category": perm.category,
                "description": perm.description,
                "is_active": perm.is_active
            } for perm in items]
            
        else:  # groups tab
            # Get total groups count
            total_docs = await Groups.all().count()
            
            # Get paginated groups with their permissions and admins count
            items = await Groups.all().offset((page - 1) * per_page).limit(per_page).prefetch_related('permissions', 'admins')
            
            # Format group data
            items = [{
                "id": group.pk,
                "name": group.name,
                "description": group.description,
                "is_active": group.is_active,
                "created_at": group.created_at,
                "permissions": [
                    {
                        "id": perm.pk,
                        "name": perm.name,
                        "code": perm.code,
                        "type": perm.type
                    } for perm in group.permissions
                ],
                "admin_count": len(group.admins)
            } for group in items]

        # Calculate pagination values
        total_pages = (total_docs + per_page - 1) // per_page
        start_page = max(1, page - 2)
        end_page = min(total_pages, page + 2)
        page_range = list(range(start_page, end_page + 1))
        start_showing = (page - 1) * per_page + 1
        end_showing = min(page * per_page, total_docs)

        # Get all groups for user creation/editing
        all_groups = []
        if tab == "users":
            all_groups = await Groups.filter(is_active=True).all()
            all_groups = [{
                "id": group.pk,
                "name": group.name
            } for group in all_groups]

        # Get permissions by category for group creation/editing
        permissions_by_category = {}
        if tab == "groups":
            permissions = await Permission.filter(is_active=True).all()
            for perm in permissions:
                if perm.category not in permissions_by_category:
                    permissions_by_category[perm.category] = []
                permissions_by_category[perm.category].append({
                    "id": perm.pk,
                    "name": perm.name,
                    "code": perm.code,
                    "type": perm.type,
                    "description": perm.description
                })

        context = {
            "request": request,
            "resources": resources,
            "page_title": "User & Group Management",
            "current_tab": tab,
            "items": items,
            "all_groups": all_groups,
            "permissions_by_category": permissions_by_category,
            "pagination": {
                "current_page": page,
                "total_pages": total_pages,
                "total_docs": total_docs,
                "per_page": per_page,
                "has_prev": page > 1,
                "has_next": page < total_pages,
                "prev_page": page - 1,
                "next_page": page + 1,
                "page_range": page_range,
                "start_showing": start_showing,
                "end_showing": end_showing,
            }
        }
        
        return templates.TemplateResponse(
            "settings.html",
            context=context
        )
    except Exception as e:
        logger.error(f"Error in settings route: {str(e)}", exc_info=True)
        return templates.TemplateResponse(
            "settings.html",
            context={
                "request": request,
                "resources": resources,
                "page_title": "User & Group Management",
                "error": f"Failed to load settings: {str(e)}",
                "current_tab": tab,
                "items": [],
                "all_groups": [],
                "permissions_by_category": {},
                "pagination": {
                    "current_page": 1,
                    "total_pages": 1,
                    "total_docs": 0,
                    "per_page": per_page,
                    "has_prev": False,
                    "has_next": False,
                    "prev_page": 1,
                    "next_page": 1,
                    "page_range": [1],
                    "start_showing": 0,
                    "end_showing": 0,
                }
            }
        )

@app.post("/settings/users/add", dependencies=[Depends(PermissionDependency(["manage_users_settings"]))])
async def add_user(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    group_id: int = Form(...),  # Changed from Form(None) to Form(...)
    admin=Depends(get_current_admin),
):
    try:
        # Check if group exists
        group = await Groups.get_or_none(pk=group_id)
        if not group:
            raise HTTPException(status_code=400, detail="Selected group does not exist")

        # Check if username already exists
        if await Admin.filter(username=username).exists():
            raise HTTPException(status_code=400, detail="Username already exists")

        # Create new user
        await Admin.create(
            username=username,
            email=email,
            password=password,
            group=group  # Ensure group is assigned
        )
        return RedirectResponse(url="/admin/settings?tab=users", status_code=HTTP_303_SEE_OTHER)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error adding user: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to add user")

@app.post("/settings/users/{user_id}/edit", dependencies=[Depends(PermissionDependency(["manage_users_settings"]))])
async def edit_user(
    request: Request,
    user_id: int,
    email: str = Form(...),
    password: str = Form(None),
    group_id: int = Form(...),  # Changed from Form(None) to Form(...)
    admin=Depends(get_current_admin),
):
    try:
        # Get user
        user = await Admin.get_or_none(pk=user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Check if group exists
        group = await Groups.get_or_none(pk=group_id)
        if not group:
            raise HTTPException(status_code=400, detail="Selected group does not exist")

        # Update user
        user.email = email
        user.group = group  # Ensure group is assigned
        if password:
            user.password = password  # Will be hashed by pre_save signal
        await user.save()
        return RedirectResponse(url="/admin/settings?tab=users", status_code=HTTP_303_SEE_OTHER)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error editing user: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to edit user")

@app.post("/settings/users/{user_id}/delete", dependencies=[Depends(PermissionDependency(["manage_users_settings"]))])
async def delete_user(
    request: Request,
    user_id: int,
    admin=Depends(get_current_admin),
):
    try:
        # Get user
        user = await Admin.get_or_none(pk=user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Don't allow deleting yourself
        if user.pk == admin.pk:
            raise HTTPException(status_code=400, detail="Cannot delete your own account")

        await user.delete()
        return RedirectResponse(url="/admin/settings?tab=users", status_code=HTTP_303_SEE_OTHER)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error deleting user: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete user")

@app.post("/settings/groups/add", dependencies=[Depends(PermissionDependency(["manage_groups_settings"]))])
async def add_group(
    request: Request,
    name: str = Form(...),
    description: str = Form(None),
    permissions: List[int] = Form([]),
    admin=Depends(get_current_admin),
):
    try:
        # Check if group name exists
        if await Groups.filter(name=name).exists():
            raise HTTPException(status_code=400, detail="Group name already exists")

        # Create new group
        group = await Groups.create(
            name=name,
            description=description,
            is_active=True
        )

        # Add permissions
        if permissions:
            perms = await Permission.filter(pk__in=permissions, is_active=True).all()
            await group.permissions.add(*perms)

        return RedirectResponse(url="/admin/settings?tab=groups", status_code=HTTP_303_SEE_OTHER)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error adding group: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to add group")

@app.post("/settings/groups/{group_id}/edit", dependencies=[Depends(PermissionDependency(["manage_groups_settings"]))])
async def edit_group(
    request: Request,
    group_id: int,
    name: str = Form(...),
    description: str = Form(None),
    permissions: List[int] = Form([]),
    is_active: bool = Form(True),
    admin=Depends(get_current_admin),
):
    try:
        # Get group
        group = await Groups.get_or_none(pk=group_id)
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")

        # Check if new name conflicts with existing groups
        if name != group.name and await Groups.filter(name=name).exists():
            raise HTTPException(status_code=400, detail="Group name already exists")

        # Update group
        group.name = name
        group.description = description
        group.is_active = is_active
        await group.save()

        # Update permissions
        current_perms = await group.permissions.all()
        await group.permissions.clear()
        if permissions:
            new_perms = await Permission.filter(pk__in=permissions, is_active=True).all()
            await group.permissions.add(*new_perms)

        return RedirectResponse(url="/admin/settings?tab=groups", status_code=HTTP_303_SEE_OTHER)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error editing group: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to edit group")

@app.post("/settings/groups/{group_id}/delete", dependencies=[Depends(PermissionDependency(["manage_groups_settings"]))])
async def delete_group(
    request: Request,
    group_id: int,
    admin=Depends(get_current_admin),
):
    try:
        # Get group
        group = await Groups.get_or_none(pk=group_id)
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")

        # Don't allow deleting group if it has users
        if await Admin.filter(group=group).exists():
            raise HTTPException(status_code=400, detail="Cannot delete group with assigned users")

        await group.delete()
        return RedirectResponse(url="/admin/settings?tab=groups", status_code=HTTP_303_SEE_OTHER)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error deleting group: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete group")

@app.post("/settings/permissions/add", dependencies=[Depends(PermissionDependency(["manage_permissions_settings"]))])
async def add_permission(
    request: Request,
    name: str = Form(...),
    code: str = Form(...),
    route: str = Form(...),
    type: str = Form(...),
    category: str = Form(...),
    description: str = Form(None),
    admin=Depends(get_current_admin),
):
    try:
        # Check if permission code exists
        if await Permission.filter(code=code).exists():
            raise HTTPException(status_code=400, detail="Permission code already exists")

        # Check if permission name exists
        if await Permission.filter(name=name).exists():
            raise HTTPException(status_code=400, detail="Permission name already exists")

        # Create new permission
        await Permission.create(
            name=name,
            code=code,
            route=route,
            type=type,
            category=category,
            description=description,
            is_active=True
        )

        return RedirectResponse(url="/admin/settings?tab=permissions", status_code=HTTP_303_SEE_OTHER)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error adding permission: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to add permission")

@app.post("/settings/permissions/{permission_id}/edit", dependencies=[Depends(PermissionDependency(["manage_permissions_settings"]))])
async def edit_permission(
    request: Request,
    permission_id: int,
    name: str = Form(...),
    code: str = Form(...),
    route: str = Form(...),
    type: str = Form(...),
    category: str = Form(...),
    description: str = Form(None),
    is_active: bool = Form(True),
    admin=Depends(get_current_admin),
):
    try:
        # Get permission
        permission = await Permission.get_or_none(pk=permission_id)
        if not permission:
            raise HTTPException(status_code=404, detail="Permission not found")

        # Check if new code conflicts with existing permissions
        if code != permission.code and await Permission.filter(code=code).exists():
            raise HTTPException(status_code=400, detail="Permission code already exists")

        # Check if new name conflicts with existing permissions
        if name != permission.name and await Permission.filter(name=name).exists():
            raise HTTPException(status_code=400, detail="Permission name already exists")

        # Update permission
        permission.name = name
        permission.code = code
        permission.route = route
        permission.type = type
        permission.category = category
        permission.description = description
        permission.is_active = is_active
        await permission.save()

        return RedirectResponse(url="/admin/settings?tab=permissions", status_code=HTTP_303_SEE_OTHER)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error editing permission: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to edit permission")

@app.post("/settings/permissions/{permission_id}/delete", dependencies=[Depends(PermissionDependency(["manage_permissions_settings"]))])
async def delete_permission(
    request: Request,
    permission_id: int,
    admin=Depends(get_current_admin),
):
    try:
        # Get permission
        permission = await Permission.get_or_none(pk=permission_id)
        if not permission:
            raise HTTPException(status_code=404, detail="Permission not found")

        # Check if permission is used by any groups
        if await permission.groups.all().count() > 0:
            raise HTTPException(status_code=400, detail="Cannot delete permission that is assigned to groups")

        await permission.delete()
        return RedirectResponse(url="/admin/settings?tab=permissions", status_code=HTTP_303_SEE_OTHER)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error deleting permission: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete permission")
