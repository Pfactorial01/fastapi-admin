import os
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from jinja2 import pass_context
from starlette.requests import Request
from starlette.templating import Jinja2Templates

from fastapi_admin import VERSION
from fastapi_admin.constants import BASE_DIR

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.globals["VERSION"] = VERSION
templates.env.globals["NOW_YEAR"] = date.today().year
templates.env.add_extension("jinja2.ext.i18n")

def datetime_filter(timestamp):
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        except ValueError:
            return timestamp
    if isinstance(timestamp, datetime):
        return timestamp.strftime('%Y-%m-%d %H:%M:%S')
    return timestamp

def format_date(value):
    """Format a date or datetime object to a string."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return value
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d')
    if isinstance(value, date):
        return value.strftime('%Y-%m-%d')
    return value

def format_currency(value, currency='USD'):
    try:
        # Convert to float if it's a string
        if isinstance(value, str):
            value = float(value)
        
        # Format with 2 decimal places and currency symbol
        if currency == 'USD':
            return f"${value:,.2f}"
        elif currency == 'EUR':
            return f"€{value:,.2f}"
        elif currency == 'GBP':
            return f"£{value:,.2f}"
        else:
            return f"{currency} {value:,.2f}"
    except (ValueError, TypeError):
        return value

def format_number(value, decimals=0):
    try:
        # Convert to float if it's a string
        if isinstance(value, str):
            value = float(value)
        
        # Format with specified decimal places and thousands separator
        return f"{value:,.{decimals}f}"
    except (ValueError, TypeError):
        return value

templates.env.filters['datetime'] = datetime_filter
templates.env.filters['format_date'] = format_date
templates.env.filters['format_currency'] = format_currency
templates.env.filters['format_number'] = format_number

@pass_context
def current_page_with_params(context: dict, params: dict):
    request = context.get("request")  # type:Request
    full_path = request.scope["raw_path"].decode()
    query_params = dict(request.query_params)
    for k, v in params.items():
        query_params[k] = v
    return full_path + "?" + urlencode(query_params)


templates.env.filters["current_page_with_params"] = current_page_with_params


def set_global_env(name: str, value: Any):
    templates.env.globals[name] = value


def add_template_folder(*folders: str):
    for folder in folders:
        templates.env.loader.searchpath.insert(0, folder)
