import datetime

from tortoise import Model, fields

from examples.enums import ProductType, Status
from fastapi_admin.models import AbstractAdmin


class Groups(Model):
    name = fields.CharField(max_length=50, unique=True, description="Group name")
    description = fields.TextField(null=True, description="Group description")
    
    # Permissions
    can_view_users = fields.BooleanField(default=False, description="Can view users")
    can_manage_users = fields.BooleanField(default=False, description="Can manage users")
    can_chat_users = fields.BooleanField(default=False, description="Can chat with users")
    can_view_properties = fields.BooleanField(default=False, description="Can view properties")
    can_manage_properties = fields.BooleanField(default=False, description="Can manage properties")
    can_manage_showing_requests = fields.BooleanField(default=False, description="Can manage showing requests")
    can_manage_groups = fields.BooleanField(default=False, description="Can manage groups")
    can_view_audit_logs = fields.BooleanField(default=False, description="Can view audit logs")
    can_manage_notifications = fields.BooleanField(default=False, description="Can manage notifications")
    
    # System fields
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    is_active = fields.BooleanField(default=True, description="Whether this group is active")
    
    class Meta:
        table = "groups"
    
    def __str__(self):
        return self.name


class Admin(AbstractAdmin):
    last_login = fields.DatetimeField(description="Last Login", default=datetime.datetime.now)
    email = fields.CharField(max_length=200, default="")
    avatar = fields.CharField(max_length=200, default="")
    intro = fields.TextField(default="")
    created_at = fields.DatetimeField(auto_now_add=True)
    group = fields.ForeignKeyField('models.Groups', related_name='admins', null=True, description="Admin's permission group")

    def __str__(self):
        return f"{self.pk}#{self.username}"


class Category(Model):
    slug = fields.CharField(max_length=200)
    name = fields.CharField(max_length=200)
    created_at = fields.DatetimeField(auto_now_add=True)


class Product(Model):
    categories = fields.ManyToManyField("models.Category")
    name = fields.CharField(max_length=50)
    view_num = fields.IntField(description="View Num")
    sort = fields.IntField()
    is_reviewed = fields.BooleanField(description="Is Reviewed")
    type = fields.IntEnumField(ProductType, description="Product Type")
    image = fields.CharField(max_length=200)
    body = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True)


class Config(Model):
    label = fields.CharField(max_length=200)
    key = fields.CharField(max_length=20, unique=True, description="Unique key for config")
    value = fields.JSONField()
    status: Status = fields.IntEnumField(Status, default=Status.on)
