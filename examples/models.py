import datetime

from tortoise import Model, fields

from examples.enums import ProductType, Status
from fastapi_admin.models import AbstractAdmin
from enum import Enum

class PermissionType(str, Enum):
    VIEW = "view"
    MANAGE = "manage"


class Permission(Model):
    name = fields.CharField(max_length=100, unique=True, description="Permission name")
    code = fields.CharField(max_length=100, unique=True, description="Permission code")
    route = fields.CharField(max_length=100, description="Permission route")
    description = fields.TextField(null=True, description="Permission description")
    category = fields.CharField(max_length=50, description="Permission category")
    type = fields.CharEnumField(PermissionType, description="Permission type")
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    is_active = fields.BooleanField(default=True, description="Whether this permission is active")

    class Meta:
        table = "permissions"

    def __str__(self):
        return self.name


class GroupPermission(Model):
    groups = fields.ForeignKeyField('models.Groups', related_name='group_permissions')
    permission = fields.ForeignKeyField('models.Permission', related_name='group_permissions')
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "group_permissions"
        unique_together = (("groups", "permission"),)


class Groups(Model):
    name = fields.CharField(max_length=50, unique=True, description="Group name")
    description = fields.TextField(null=True, description="Group description")
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    is_active = fields.BooleanField(default=True, description="Whether this group is active")
    permissions = fields.ManyToManyField(
        'models.Permission', 
        through='group_permissions', 
        related_name='groups',
        description="Group permissions"
    )

    class Meta:
        table = "groups"

    def __str__(self):
        return self.name

    async def has_permission(self, permission_code: str) -> bool:
        """Check if group has a specific permission"""
        return await self.permissions.filter(
            code=permission_code,
            is_active=True
        ).exists()


class Admin(AbstractAdmin):
    last_login = fields.DatetimeField(description="Last Login", default=datetime.datetime.now)
    email = fields.CharField(max_length=200, default="")
    avatar = fields.CharField(max_length=200, default="")
    intro = fields.TextField(default="")
    created_at = fields.DatetimeField(auto_now_add=True)
    group = fields.ForeignKeyField('models.Groups', related_name='admins', null=True, description="Admin's permission group")

    def __str__(self):
        return f"{self.pk}#{self.username}"

    async def has_permission(self, permission_code: str) -> bool:
        """Check if admin has a specific permission through their group"""
        if not self.group:
            return False
        return await self.group.has_permission(permission_code)


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

class Subscription(Model):
    # Basic subscription info
    charge_id = fields.CharField(max_length=200, unique=True, description="Charge ID")
    transaction_id = fields.CharField(max_length=200, unique=True, description="Transaction ID")
    status = fields.CharField(max_length=50, description="active, expired, canceled, past_due, refunded")
    package_type = fields.CharField(max_length=100, description="Human readable tier name")
    
    # Billing details
    amount = fields.IntField(description="Subscription amount in cents")
    currency = fields.CharField(max_length=3, default="USD")
    billing_cycle = fields.CharField(max_length=20, description="monthly, yearly, etc", null=True)
    next_billing_date = fields.DatetimeField(description="When the next charge should occur", null=True)
    last_billing_date = fields.DatetimeField(description="When the last charge occurred", null=True)
    
    # Relationships
    user_id = fields.CharField(max_length=200, null=True)  
    property_id = fields.CharField(max_length=200, null=True)  
    
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "subscriptions"

    def __str__(self):
        return f"Subscription {self.id} - {self.package_type}"

class StripeWebhookLog(Model):
    event_id = fields.CharField(max_length=200, unique=True, description="Stripe Event ID")
    event_type = fields.CharField(max_length=100, description="Type of Stripe event")
    api_version = fields.CharField(max_length=50, description="Stripe API version")
    created = fields.DatetimeField(description="When the event was created")
    livemode = fields.BooleanField(description="Whether this was a live mode event")
    request_id = fields.CharField(max_length=200, null=True, description="Stripe request ID")
    idempotency_key = fields.CharField(max_length=200, null=True, description="Idempotency key if provided")
    data = fields.JSONField(description="Full event data")
    processed = fields.BooleanField(default=False, description="Whether this webhook was processed")
    processing_errors = fields.TextField(null=True, description="Any errors during processing")
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "stripe_webhook_logs"

    def __str__(self):
        return f"{self.event_type} - {self.event_id}"


