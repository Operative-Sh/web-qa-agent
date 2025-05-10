#!/usr/bin/env python3

import asyncio
import os
import argparse
import traceback
import uuid
from enum import Enum
import subprocess
import json
from pathlib import Path
import requests

from webEvalAgent.src.utils import stop_log_server
from webEvalAgent.src.log_server import send_log

# Set the API key to a fake key to avoid error in backend
os.environ["ANTHROPIC_API_KEY"] = 'not_a_real_key'
os.environ["ANONYMIZED_TELEMETRY"] = 'false'

# MCP imports
from mcp.server.fastmcp import FastMCP, Context
from mcp.types import TextContent

# Import our modules
from webEvalAgent.src.browser_manager import PlaywrightBrowserManager
# from webEvalAgent.src.browser_utils import cleanup_resources # Removed import
from webEvalAgent.src.api_utils import validate_api_key
from webEvalAgent.src.tool_handlers import handle_web_evaluation, handle_setup_browser_state

# MCP server modules

# Stop any existing log server to avoid conflicts
# This doesn't start a new server, just ensures none is running
stop_log_server()

# Create the MCP server
mcp = FastMCP("Operative")

# Define the browser tools
class BrowserTools(str, Enum):
    WEB_EVAL_AGENT = "web_eval_agent"
    SETUP_BROWSER_STATE = "setup_browser_state"

# --- Start of new/modified functions ---

CONFIG_DIR = Path.home() / ".operative"
CONFIG_FILE = CONFIG_DIR / "config.json"
OPERATIVE_API_KEY_HOLDER = {"key": None} # Global holder for the validated API key

# ANSI color and formatting codes
RED = '\033[0;31m'
GREEN = '\033[0;32m'
BLUE = '\033[0;34m'
YELLOW = '\033[1;33m'
NC = '\033[0m' # No Color
BOLD = '\033[1m'

def ensure_playwright_browsers():
    """Checks and installs Playwright browsers if necessary."""
    try:
        # Using playwright's Python API to check/install is more robust if available.
        # For now, calling the CLI command.
        # Ensure playwright is in the path for uvx environment.
        process = subprocess.run(["playwright", "install", "--with-deps"], capture_output=True, text=True, check=False, timeout=300)
        if process.returncode == 0:
            if process.stdout:
                pass
            if process.stderr:
                pass
        
        else:
            # Not raising an error here to allow the agent to attempt to start,
            # but logging a significant warning. Tool usage will likely fail.
            pass
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        pass
    except Exception as e:
        pass

def _configure_cursor_mcp_json(agent_project_path: Path, api_key=None):
    """Attempts to automatically configure Cursor's mcp.json file."""
    cursor_mcp_file = Path.home() / ".cursor" / "mcp.json"
    server_name = "web-eval-agent-operative"
    

    mcp_config = {"mcpServers": {}}
    try:
        if cursor_mcp_file.exists():
            with open(cursor_mcp_file, 'r') as f:
                try:
                    mcp_config = json.load(f)
                    if not isinstance(mcp_config, dict):
                        mcp_config = {"mcpServers": {}}
                    if "mcpServers" not in mcp_config or not isinstance(mcp_config.get("mcpServers"), dict):
                        mcp_config["mcpServers"] = {}
                except json.JSONDecodeError:
                    try:
                        backup_path = cursor_mcp_file.with_suffix(".json.bak")
                        cursor_mcp_file.rename(backup_path)
                    except OSError as e_backup:
                        pass
                    mcp_config = {"mcpServers": {}}
        else:
            # Ensure .cursor directory exists
            cursor_mcp_file.parent.mkdir(parents=True, exist_ok=True)

        server_config = {
            "command": "uvx",
            "args": [
                "--from",
                "git+https://github.com/nandatheguntupalli/web-eval-agent.git",
                "webEvalAgent"
            ],
            "env": {}
        }
        
        # Add API key to environment variables if provided
        if api_key:
            server_config["env"]["OPERATIVE_API_KEY"] = api_key

        # Add or update the server entry
        mcp_config["mcpServers"][server_name] = server_config

        with open(cursor_mcp_file, 'w') as f:
            json.dump(mcp_config, f, indent=2)
        
        return cursor_mcp_file, mcp_config

    except OSError as e_os:
        pass
    
    return None, None

def _validate_api_key_server_side(api_key_to_validate):
    """Validates API key with the backend server."""
    try:
        response = requests.get(
            "https://operative-backend.onrender.com/api/validate-key",
            headers={"x-operative-api-key": api_key_to_validate},
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        if data.get("valid"):
            return True, data.get("message", "Valid")
        else:
            error_message = data.get("message", "Unknown validation error.")
            return False, error_message
    except requests.exceptions.Timeout:
        return False, "Connection to validation server timed out."
    except requests.exceptions.RequestException as e:
        return False, f"Could not connect to validation server: {e}"
    except json.JSONDecodeError:
        return False, "Invalid JSON response from validation server."

def get_and_validate_api_key():
    """Gets API key from env, MCP config, or prompts user, then validates and stores it."""
    api_key = os.environ.get("OPERATIVE_API_KEY")
    source = "environment variable"

    # Check MCP config first if environment variable is not set
    if not api_key:
        cursor_mcp_file = Path.home() / ".cursor" / "mcp.json"
        server_name = "web-eval-agent-operative"
        
        if cursor_mcp_file.exists():
            try:
                with open(cursor_mcp_file, 'r') as f:
                    mcp_config = json.load(f)
                    if (isinstance(mcp_config, dict) and 
                        "mcpServers" in mcp_config and 
                        isinstance(mcp_config["mcpServers"], dict) and
                        server_name in mcp_config["mcpServers"] and
                        "env" in mcp_config["mcpServers"][server_name] and
                        "OPERATIVE_API_KEY" in mcp_config["mcpServers"][server_name]["env"]):
                        api_key = mcp_config["mcpServers"][server_name]["env"]["OPERATIVE_API_KEY"]
                        source = "MCP config file"
            except (json.JSONDecodeError, OSError):
                pass
    
    # Check legacy config file if MCP config doesn't have the key
    if not api_key:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():
            try:
                config_data = json.loads(CONFIG_FILE.read_text())
                api_key = config_data.get("OPERATIVE_API_KEY")
                source = "legacy config file"
            except json.JSONDecodeError:
                pass
        
    if api_key:
        is_valid, msg = _validate_api_key_server_side(api_key)
        if is_valid:
            OPERATIVE_API_KEY_HOLDER["key"] = api_key
            return api_key
        else:
            api_key = None # Reset api_key to trigger prompt

    # Prompt user if no valid key found yet
    while True:
        try:
            prompted_key = input("Please enter your Operative.sh API key: ")
        except EOFError: # Happens if stdin is not available (e.g. background process)
             raise ValueError("API Key could not be obtained via input. Configure it via environment or MCP config.")

        if not prompted_key:
            continue
        
        is_valid, msg = _validate_api_key_server_side(prompted_key)
        if is_valid:
            # Save API key to MCP config
            cursor_mcp_file = Path.home() / ".cursor" / "mcp.json"
            server_name = "web-eval-agent-operative"
            try:
                # Update MCP config with API key
                mcp_file, mcp_config = _configure_cursor_mcp_json(Path(".").resolve(), prompted_key)
                if mcp_file:
                    pass
                else:
                    # Fallback to legacy config if MCP config update fails
                    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                    CONFIG_FILE.write_text(json.dumps({"OPERATIVE_API_KEY": prompted_key}))
                    pass
            except Exception as e:
                pass
            
            OPERATIVE_API_KEY_HOLDER["key"] = prompted_key
            return prompted_key
        else:
    
            # Provide a way to exit if user doesn't want to retry
            retry = input("Invalid API key. Would you like to try again? (y/n): ")
            if retry.lower() != 'y':

                raise ValueError("Invalid API key and user chose not to retry.")

@mcp.tool(name=BrowserTools.WEB_EVAL_AGENT)
async def web_eval_agent(url: str, task: str, ctx: Context, headless_browser: bool = False) -> list[TextContent]:
    """Evaluate the user experience / interface of a web application.

    This tool allows the AI to assess the quality of user experience and interface design
    of a web application by performing specific tasks and analyzing the interaction flow.

    Before this tool is used, the web application should already be running locally on a port.

    Args:
        url: Required. The localhost URL of the web application to evaluate, including the port number.
            Example: http://localhost:3000, http://localhost:8080, http://localhost:4200, http://localhost:5173, etc.
            Try to avoid using the path segments of the URL, and instead use the root URL.
        task: Required. The specific UX/UI aspect to test (e.g., "test the checkout flow",
             "evaluate the navigation menu usability", "check form validation feedback")
             Be as detailed as possible in your task description. It could be anywhere from 2 sentences to 2 paragraphs.
        headless_browser: Optional. Whether to hide the browser window popup during evaluation.
        If headless_browser is True, only the operative control center browser will show, and no popup browser will be shown.

    Returns:
        list[list[TextContent, ImageContent]]: A detailed evaluation of the web application's UX/UI, including
                         observations, issues found, and recommendations for improvement
                         and screenshots of the web application during the evaluation
    """
    headless = headless_browser
    api_key = OPERATIVE_API_KEY_HOLDER["key"]
    is_valid = await validate_api_key(api_key)

    if not is_valid:
        error_message_str = "❌ Error: API Key validation failed when running the tool.\n"
        error_message_str += "   Reason: Free tier limit reached.\n"
        error_message_str += "   👉 Please subscribe at https://operative.sh to continue."
        return [TextContent(type="text", text=error_message_str)]
    try:
        tool_call_id = str(uuid.uuid4())
        send_log(f"Generated new tool_call_id for setup_browser_state: {tool_call_id}")
        return await handle_web_evaluation(
            {"url": url, "task": task, "headless": headless, "tool_call_id": tool_call_id},
            ctx,
            api_key
        )
    except Exception as e:
        tb = traceback.format_exc()

        return [TextContent(
            type="text",
            text=f"Error executing web_eval_agent: {str(e)}\n\nTraceback:\n{tb}"
        )]

@mcp.tool(name=BrowserTools.SETUP_BROWSER_STATE)
async def setup_browser_state(url: str = None, ctx: Context = None) -> list[TextContent]:
    """Sets up and saves browser state for future use.

    This tool should only be called in one scenario:
    1. The user explicitly requests to set up browser state/authentication

    Launches a non-headless browser for user interaction, allows login/authentication,
    and saves the browser state (cookies, local storage, etc.) to a local file.

    Args:
        url: Optional URL to navigate to upon opening the browser.
        ctx: The MCP context (used for progress reporting, not directly here).

    Returns:
        list[TextContent]: Confirmation of state saving or error messages.
    """
    api_key = OPERATIVE_API_KEY_HOLDER["key"]
    is_valid = await validate_api_key(api_key)

    if not is_valid:
        error_message_str = "❌ Error: API Key validation failed when running the tool.\n"
        error_message_str += "   Reason: Free tier limit reached.\n"
        error_message_str += "   👉 Please subscribe at https://operative.sh to continue."
        return [TextContent(type="text", text=error_message_str)]
    try:
        tool_call_id = str(uuid.uuid4())
        return await handle_setup_browser_state(
            {"url": url, "tool_call_id": tool_call_id},
            ctx,
            api_key
        )
    except Exception as e:
        tb = traceback.format_exc()
        return [TextContent(
            type="text",
            text=f"Error executing setup_browser_state: {str(e)}\n\nTraceback:\n{tb}"
        )]

def main():
    try:
        # Determine agent's project path for MCP configuration
        try:
            agent_project_path = Path(__file__).resolve().parent.parent
        except NameError:
            agent_project_path = Path(".").resolve()
        cursor_mcp_file = Path.home() / ".cursor" / "mcp.json"
        server_name = "web-eval-agent-operative"
        is_already_configured = False
        if cursor_mcp_file.exists():
            try:
                with open(cursor_mcp_file, 'r') as f:
                    mcp_config = json.load(f)
                    if (isinstance(mcp_config, dict) and 
                        "mcpServers" in mcp_config and 
                        isinstance(mcp_config["mcpServers"], dict) and
                        server_name in mcp_config["mcpServers"]):
                        is_already_configured = True
            except (json.JSONDecodeError, OSError):
                pass
        operative_key = get_and_validate_api_key()
        if not operative_key:
            return
        if not is_already_configured:
            _configure_cursor_mcp_json(agent_project_path, operative_key)
            ensure_playwright_browsers()
            return
        else:
            mcp.run(transport='stdio')
    except Exception:
        pass

if __name__ == "__main__":
    main() # Call the main function which now includes setup.
