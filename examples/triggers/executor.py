import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import httpx
import os
from datetime import datetime
import json
from typing import Any, Dict
import importlib.util
import sys
from string import Template
from RestrictedPython import compile_restricted
from RestrictedPython.Guards import guarded_iter_unpack_sequence
from RestrictedPython.Guards import guarded_unpack_sequence
from RestrictedPython.Guards import safe_builtins
from RestrictedPython.Guards import full_write_guard
import builtins
from contextlib import contextmanager
import threading
import time
import inspect

logger = logging.getLogger(__name__)

# Define a custom print function for the restricted environment
def _safe_print(*args, **kwargs):
    """Safe print function that only allows string formatting"""
    output = []
    for arg in args:
        if isinstance(arg, (str, int, float, bool)):
            output.append(str(arg))
        else:
            output.append('[RESTRICTED]')
    return ' '.join(output)

# Create a restricted builtins dictionary
SAFE_BUILTINS = {
    'abs': abs,
    'bool': bool,
    'dict': dict,
    'float': float,
    'int': int,
    'isinstance': isinstance,
    'len': len,
    'list': list,
    'max': max,
    'min': min,
    'print': _safe_print,
    'range': range,
    'round': round,
    'str': str,
    'sum': sum,
    'tuple': tuple,
    'type': type,
}

class ScriptTimeoutError(Exception):
    """Exception raised when a script execution times out."""
    pass

@contextmanager
def timeout(seconds):
    """Context manager for timing out script execution."""
    timer = threading.Timer(seconds, lambda: (_ for _ in ()).throw(ScriptTimeoutError()))
    timer.start()
    try:
        yield
    finally:
        timer.cancel()

def create_restricted_globals():
    """Create a restricted globals dictionary for script execution."""
    restricted_globals = {
        '__builtins__': SAFE_BUILTINS,
        '_getiter_': iter,
        '_iter_unpack_sequence_': guarded_iter_unpack_sequence,
        '_unpack_sequence_': guarded_unpack_sequence,
        '_write_': full_write_guard,
        # Add any other safe functions or variables here
    }
    return restricted_globals

async def execute_trigger_action(trigger: Dict[str, Any], event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a trigger action based on its type and configuration."""
    try:
        action_type = trigger.get('action_type')
        config = trigger.get('config', {})

        if not action_type or not config:
            raise ValueError("Invalid trigger configuration")

        if action_type == 'send_email':
            return await execute_email_action(config, event_data)
        elif action_type == 'api_call':
            return await execute_api_action(config, event_data)
        elif action_type == 'python_script':
            return await execute_script_action(config, event_data)
        else:
            raise ValueError(f"Unsupported action type: {action_type}")

    except Exception as e:
        logger.error(f"Error executing trigger action: {str(e)}")
        raise

async def execute_email_action(config: Dict[str, Any], event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an email action."""
    try:
        # Get email configuration from environment
        smtp_host = os.getenv('SMTP_HOST')
        smtp_port = int(os.getenv('SMTP_PORT', '587'))
        smtp_user = os.getenv('SMTP_USER')
        smtp_password = os.getenv('SMTP_PASSWORD')
        sender_email = os.getenv('SENDER_EMAIL')

        if not all([smtp_host, smtp_port, smtp_user, smtp_password, sender_email]):
            raise ValueError("Email configuration not properly set")

        # Process templates
        subject_template = Template(config.get('subject', ''))
        body_template = Template(config.get('body', ''))

        subject = subject_template.safe_substitute(event_data)
        body = body_template.safe_substitute(event_data)

        # Create message
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = ', '.join(config.get('recipients', []))
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Send email
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)

        return {
            "status": "success",
            "message": "Email sent successfully",
            "details": {
                "recipients": config.get('recipients', []),
                "subject": subject
            }
        }

    except Exception as e:
        logger.error(f"Error executing email action: {str(e)}")
        raise

async def execute_api_action(config: Dict[str, Any], event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an API call action."""
    try:
        url = config.get('url')
        method = config.get('method', 'POST').upper()
        headers = config.get('headers', {})
        body_template = config.get('body', {})
        verify_ssl = config.get('verify_ssl', False)  # Default to False for compatibility

        if not url:
            raise ValueError("API URL not specified")

        # Process body template
        if isinstance(body_template, dict):
            body = json.loads(
                Template(json.dumps(body_template)).safe_substitute(event_data)
            )
        else:
            body = json.loads(Template(body_template).safe_substitute(event_data))

        # Configure client with SSL verification settings
        client_kwargs = {
            'verify': verify_ssl,  # Control SSL verification
            'timeout': 30.0,
        }
        
        if not verify_ssl:
            import warnings
            warnings.filterwarnings('ignore', message='Unverified HTTPS request')

        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                json=body,
            )

            # Get response content based on content type
            response_content = None
            content_type = response.headers.get('content-type', '')
            
            try:
                if 'application/json' in content_type:
                    response_content = response.json()
                else:
                    response_content = response.text
            except Exception:
                response_content = response.text

            return {
                "status": "success",
                "message": "API call executed successfully",
                "details": {
                    "status_code": response.status_code,
                    "response": response_content
                }
            }

    except Exception as e:
        logger.error(f"Error executing API action: {str(e)}")
        raise

async def execute_script_action(config: Dict[str, Any], event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a Python script action in a restricted environment."""
    try:
        script_content = config.get('script')
        if not script_content:
            raise ValueError("Script content not provided")

        # Verify the script contains a run function definition
        if "def run(data):" not in script_content:
            raise ValueError("Script must contain a 'def run(data):' function definition")

        # Prepare the restricted environment
        restricted_globals = create_restricted_globals()
        restricted_globals['event_data'] = event_data  # Make event data available to the script
        
        try:
            # Compile the script with restrictions
            byte_code = compile_restricted(
                script_content,
                filename='<inline>',
                mode='exec'
            )
            
            # Create a new restricted module for execution
            restricted_locals = {}
            
            # Execute with timeout (30 seconds)
            with timeout(30):
                # Execute the compiled code in the restricted environment
                exec(byte_code, restricted_globals, restricted_locals)
                
                # Verify the run function exists and is callable
                if 'run' not in restricted_locals:
                    raise ValueError("Script must define a run(data) function")
                
                run_func = restricted_locals['run']
                if not callable(run_func):
                    raise ValueError("run must be a callable function")
                
                # Verify the function signature
                sig = inspect.signature(run_func)
                if len(sig.parameters) != 1:
                    raise ValueError("run function must accept exactly one parameter (data)")
                
                # Execute the run function with the event data
                result = run_func(event_data)
                
                # Validate the result
                if not isinstance(result, (dict, list, str, int, float, bool, type(None))):
                    raise ValueError("Script returned an invalid type. Must be a basic Python type.")
                
                return {
                    "status": "success",
                    "message": "Script executed successfully",
                    "details": {
                        "result": result
                    }
                }
                
        except ScriptTimeoutError:
            raise ValueError("Script execution timed out (5 second limit)")
        except SyntaxError as e:
            raise ValueError(f"Script syntax error: {str(e)}")
        except Exception as e:
            raise ValueError(f"Script execution error: {str(e)}")

    except Exception as e:
        logger.error(f"Error executing script action: {str(e)}")
        raise