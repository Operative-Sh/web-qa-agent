#!/usr/bin/env python3

import asyncio
import json
import logging
import uuid
import warnings
import os
from typing import Dict, Any, List, Optional
from collections import deque
import pathlib # Added for file reading

# Import log server function
from .log_server import send_log

# Import Playwright types
from playwright.async_api import async_playwright, Error as PlaywrightError, Page as PlaywrightPage

# Browser-use imports
from browser_use.agent.service import Agent
from browser_use.browser.browser import Browser, BrowserConfig
from browser_use.browser.context import BrowserContext # Import BrowserContext

# Langchain/MCP imports
from langchain_anthropic import ChatAnthropic
from langchain.globals import set_verbose

# Original method will be stored here
_original_bring_to_front = None

# This prevents the browser window from stealing focus during execution.
async def _no_bring_to_front(self, *args, **kwargs):
    return None

# Global variables
agent_instance = None  # Store agent instance
original_create_context: Optional[callable] = None  # Store original patched method
active_cdp_session = None  # Store active CDP session for input handling
active_screencast_running = False  # Track if screencast is running
browser_task_loop = None  # Store the asyncio loop used by run_browser_task
screenshot_task = None  # Store the periodic screenshot task

# Define the maximum number of logs/requests to keep
MAX_LOG_ENTRIES = 1000  # Increased from 10 to allow more log entries

# --- URL Filtering for Network Requests ---
def should_log_network_request(request) -> bool:
    """Determine if a network request should be logged based on its type and URL.
    
    Args:
        request: The Playwright request object
        
    Returns:
        bool: True if the request should be logged, False if it should be filtered out
    """
    url = request.url
    if '/node_modules/' in url:
        return False
    
    # Only log XHR requests
    if request.resource_type != 'xhr' and request.resource_type != 'fetch':
        return False
        
    # Skip common static file types
    extensions_to_filter = [
        '.js', '.css', '.woff', '.woff2', '.ttf', '.eot', '.svg', '.png', 
        '.jpg', '.jpeg', '.gif', '.ico', '.map'
    ]
    
    for ext in extensions_to_filter:
        if url.endswith(ext) or f"{ext}?" in url:  # Handle URLs with query params
            return False
    
    # By default, log all XHR requests that weren't filtered
    return True

# --- Log Storage (Global within this module using deque) ---
console_log_storage: deque = deque(maxlen=MAX_LOG_ENTRIES)
network_request_storage: deque = deque(maxlen=MAX_LOG_ENTRIES)

# --- Screenshot Storage (Global within this module) ---
screenshot_storage: List[Dict[str, Any]] = []

# --- Log Handlers (Use deque's append and send_log with type) ---
# Async handler functions
async def _handle_console_message(message):
    try:
        text = message.text
        log_entry = { "type": message.type, "text": text, "location": message.location, "timestamp": asyncio.get_event_loop().time() }
        console_log_storage.append(log_entry)
        
        # Check if message has a failure attribute
        if hasattr(message, 'failure') and message.failure:
            send_log(f"CONSOLE ERROR [{log_entry['type']}]: {log_entry['text']} - {message.failure}", "❌", log_type='console')
        else:
            send_log(f"CONSOLE [{log_entry['type']}]: {log_entry['text']}", "🖥️", log_type='console')
    except Exception as e:
        send_log(f"Error handling console message: {e}", "❌", log_type='status')

async def _handle_request(request):
    try:
        if not should_log_network_request(request):
            return
            
        try: headers = await request.all_headers()
        except PlaywrightError as e: headers = {"error": f"Req Header Error: {e}"}
        except Exception as e: headers = {"error": f"Unexpected Req Header Error: {e}"}

        post_data = None
        try:
            if request.post_data:
                 post_data_buffer = await request.post_data_buffer()
                 if post_data_buffer:
                     try: post_data = post_data_buffer.decode('utf-8', errors='replace')
                     except Exception: post_data = repr(post_data_buffer)
                 else: post_data = ""
            else: post_data = None
        except PlaywrightError as e: post_data = f"Post Data Error: {e}"
        except Exception as e: post_data = f"Unexpected Post Data Error: {e}"

        request_entry = { "url": request.url, "method": request.method, "headers": headers, "postData": post_data, "timestamp": asyncio.get_event_loop().time(), "resourceType": request.resource_type, "is_navigation": request.is_navigation_request(), "id": id(request) }
        network_request_storage.append(request_entry)
        send_log(f"NET REQ [{request_entry['method']}]: {request_entry['url']}", "➡️", log_type='network')
    except Exception as e:
        url = request.url if request else 'Unknown URL'
        send_log(f"Error handling request event for {url}: {e}", "❌", log_type='status')

async def _handle_response(response):
    req_id = id(response.request)
    url = response.url
    
    if not should_log_network_request(response.request):
        return
        
    try:
        try: 
            headers = await response.all_headers()
            # Check if content type is JSON
            content_type = headers.get('content-type', '').lower()
            if not ('application/json' in content_type or '+json' in content_type):
                return  # Skip non-JSON responses
        except PlaywrightError as e: 
            headers = {"error": f"Resp Header Error: {e}"}
        except Exception as e: 
            headers = {"error": f"Unexpected Resp Header Error: {e}"}
        
        status = response.status

        body_size = -1
        try:
            body_buffer = await response.body()
            body_size = len(body_buffer) if body_buffer else 0
        except Exception: pass

        for req in network_request_storage:
            if req.get("id") == req_id and "response_status" not in req:
                req["response_status"] = status
                req["response_headers"] = headers
                req["response_body_size"] = body_size
                req["response_timestamp"] = asyncio.get_event_loop().time()
                send_log(f"NET RESP [{status}]: {url} (JSON)", "⬅️", log_type='network')
                break
        else:
            send_log(f"NET RESP* [{status}]: {url} (JSON, req not matched/updated)", "⬅️", log_type='network')
    except Exception as e:
        send_log(f"Error handling response event for {url}: {e}", "❌", log_type='status')

async def _handle_page_error(error):
    try:
        error_text = f"PAGE ERROR: {error}"
        send_log(error_text, "🐛", log_type='console')
        # Add to console_log_storage with type 'error'
        console_log_storage.append({
            "type": "error",
            "text": error_text,
            "location": None,
            "timestamp": asyncio.get_event_loop().time()
        })
    except Exception as e:
        send_log(f"Error handling page error: {e}", "❌", log_type='status')

async def _handle_web_error(error):
    try:
        error_text = f"JS ERROR: {error.error}: {error.page}"
        send_log(error_text, "🐛", log_type='console')
        # Add to console_log_storage with type 'error'
        console_log_storage.append({
            "type": "error",
            "text": error_text,
            "location": error.page.url if hasattr(error.page, 'url') else None,
            "timestamp": asyncio.get_event_loop().time()
        })
    except Exception as e:
        send_log(f"Error handling web error: {e}", "❌", log_type='status')

async def _handle_request_failed(error):
    try:
        error_text = f"REQUEST FAILED: {error}"
        send_log(error_text, "🐛", log_type='console')
        # Add to console_log_storage with type 'error'
        console_log_storage.append({
            "type": "error",
            "text": error_text,
            "location": None,
            "timestamp": asyncio.get_event_loop().time()
        })
    except Exception as e:
        send_log(f"Error handling request failed: {e}", "❌", log_type='status')

# Non-async wrapper functions for event listeners
def handle_console_message(message):
    asyncio.create_task(_handle_console_message(message))

def handle_request(request):
    asyncio.create_task(_handle_request(request))

def handle_response(response):
    asyncio.create_task(_handle_response(response))

def handle_page_error(error):
    asyncio.create_task(_handle_page_error(error))

def handle_web_error(error):
    asyncio.create_task(_handle_web_error(error))

def handle_request_failed(error):
    asyncio.create_task(_handle_request_failed(error))

# Read the JavaScript overlay code from the file
try:
    overlay_js_path = pathlib.Path(__file__).parent / 'agent_overlay.js'
    AGENT_CONTROL_OVERLAY_JS = overlay_js_path.read_text(encoding='utf-8')
except Exception as e:
    send_log(f"CRITICAL ERROR: Failed to read agent_overlay.js: {e}", "🚨", log_type='status')
    AGENT_CONTROL_OVERLAY_JS = "console.error('Failed to load agent overlay script');" # Fallback

# Function to inject the agent control overlay into a page
async def inject_agent_control_overlay(page: PlaywrightPage):
    """Inject the agent control overlay into a page."""
    try:
        # First try with evaluate
        try:
            await page.evaluate(AGENT_CONTROL_OVERLAY_JS)
            return
        except Exception as e1:
            send_log(f"Failed to inject with page.evaluate(): {e1}", "⚠️", log_type='status')
            
        # Try with add_script_tag as fallback
        try:
            await page.add_script_tag(content=AGENT_CONTROL_OVERLAY_JS)
            return
        except Exception as e2:
            send_log(f"Failed to inject with page.add_script_tag(): {e2}", "⚠️", log_type='status')
            
        # Try with evaluate_handle as last resort
        try:
            await page.evaluate_handle(f"() => {{ {AGENT_CONTROL_OVERLAY_JS} }}")
            return
        except Exception as e3:
            send_log(f"Failed to inject with page.evaluate_handle(): {e3}", "⚠️", log_type='status')
            raise Exception(f"All injection methods failed: {e1}, {e2}, {e3}")
            
    except Exception as e:
        send_log(f"Failed to inject agent control overlay: {e}", "❌", log_type='status')

# Function to set up agent control functions for a page
async def setup_page_agent_controls(page: PlaywrightPage):
    """Set up agent control functions for a page."""
    global agent_instance
    
    try:
        # Expose agent control functions to the page
        await page.expose_function('pauseAgent', lambda: pause_agent())
        await page.expose_function('resumeAgent', lambda: resume_agent())
        await page.expose_function('stopAgent', lambda: stop_agent())
        await page.expose_function('getAgentState', lambda: get_agent_state())
        
        # Add navigation listener to re-inject overlay after navigation
        async def handle_frame_navigation(frame):
            if frame is page.main_frame:
                send_log(f"Page navigated to: {page.url}", "🧭", log_type='status')
        
        # Define async wrapper functions for event listeners
        page.on("framenavigated", lambda frame: asyncio.create_task(handle_frame_navigation(frame)))
        send_log("Added navigation listener to page", "🔄", log_type='status')
        
        # Also listen for load events to re-inject the overlay
        async def handle_load():
            send_log(f"Page load event on: {page.url}", "🔄", log_type='status')
            await asyncio.sleep(0.5)  # Wait a bit for the page to stabilize
            
        page.on("load", lambda: asyncio.create_task(handle_load()))
        send_log("Added load event listener to page", "🔄", log_type='status')
        
    except Exception as e:
        send_log(f"Failed to set up agent controls: {e}", "❌", log_type='status')

# Agent control functions
def pause_agent():
    """Pause the agent."""
    global agent_instance
    if agent_instance:
        agent_instance.pause()
        send_log("Agent paused", "⏸️", log_type='status')
        # Send agent state update to frontend
        from .log_server import socketio
        socketio.emit('agent_state', {'state': {'paused': True, 'stopped': False}})
        return True
    return False

def resume_agent():
    """Resume the agent."""
    global agent_instance
    if agent_instance:
        agent_instance.resume()
        send_log("Agent resumed", "▶️", log_type='status')
        # Send agent state update to frontend
        from .log_server import socketio
        socketio.emit('agent_state', {'state': {'paused': False, 'stopped': False}})
        return True
    return False

def stop_agent():
    """Stop the agent."""
    global agent_instance
    if agent_instance:
        agent_instance.stop()
        send_log("Agent stopped", "⏹️", log_type='status')
        # Send agent state update to frontend
        from .log_server import socketio
        socketio.emit('agent_state', {'state': {'paused': False, 'stopped': True}})
        return True
    return False

def get_agent_state():
    """Get the agent state."""
    global agent_instance
    state = {
        'paused': False,
        'stopped': False
    }
    
    if agent_instance and hasattr(agent_instance, 'state'):
        state = {
            'paused': agent_instance.state.paused,
            'stopped': agent_instance.state.stopped
        }
    
    # Send agent state update to frontend
    try:
        from .log_server import socketio
        socketio.emit('agent_state', {'state': state})
    except Exception:
        pass
        
    return state

# Function to get the browser task loop
def get_browser_task_loop():
    """Get the asyncio loop used by run_browser_task."""
    global browser_task_loop
    return browser_task_loop

# --- Input Handling Functions ---
async def handle_browser_input(event_type: str, details: Dict) -> None:
    """Handle browser input events from the frontend.
    
    Args:
        event_type: The type of input event (click, scroll, keydown, keyup)
        details: The details of the input event
        
    Returns:
        None
    """
    global active_cdp_session, active_screencast_running
    
    if event_type != 'scroll':
        send_log(f"handle_browser_input called with event_type: {event_type}", "🔍", log_type='status')
    
    # Check if we have an active CDP session
    if not active_cdp_session:
        send_log("Input error: No active CDP session", "❌", log_type='status')
        return
        
    # Check if screencast is running
    if not active_screencast_running:
        send_log("Input error: Screencast not running", "❌", log_type='status')
        return

    if event_type != 'scroll':
        send_log(f"Processing input: {event_type}", "🔄", log_type='status')

    try:
        if event_type == 'click':
            # CDP expects separate press and release events for a click
            button = details.get('button', 'left')
            x = details.get('x', 0)
            y = details.get('y', 0)
            click_count = details.get('clickCount', 1)
            # Modifiers might be needed for complex interactions, but start simple
            modifiers = 0 # TODO: Map ctrlKey, shiftKey etc. if needed
            
            # Mouse Pressed
            mouse_pressed_params = {
                "type": "mousePressed",
                "button": button,
                "x": x,
                "y": y,
                "modifiers": modifiers,
                "clickCount": click_count
            }
            
            # send_log(f"Dispatching mousePressed at ({x},{y})", "➡️", log_type='status')
            try:
                await active_cdp_session.send("Input.dispatchMouseEvent", mouse_pressed_params)
                send_log("mousePressed dispatched successfully", "✅", log_type='status')
            except Exception as press_error:
                send_log(f"Input error: Failed to send mousePressed: {press_error}", "❌", log_type='status')
                return
            
            # Short delay often helps reliability
            await asyncio.sleep(0.05)
            
            # Mouse Released
            mouse_released_params = {
                "type": "mouseReleased",
                "button": button,
                "x": x,
                "y": y,
                "modifiers": modifiers,
                "clickCount": click_count
            }
            
            # send_log(f"Dispatching mouseReleased at ({x},{y})", "➡️", log_type='status')
            try:
                await active_cdp_session.send("Input.dispatchMouseEvent", mouse_released_params)
                send_log("mouseReleased dispatched successfully", "✅", log_type='status')
            except Exception as release_error:
                send_log(f"Input error: Failed to send mouseReleased: {release_error}", "❌", log_type='status')
                return
            
            send_log(f"Click sent at ({x},{y})", "👆", log_type='status')

        elif event_type == 'keydown':
            # Map frontend details to CDP key event parameters
            key = details.get('key', '')
            code = details.get('code', '')
            modifiers = _map_modifiers(details)
            
            # For keyDown events, include the 'text' parameter for printable characters
            # This is necessary for text to appear in input fields
            key_params = {
                "type": "keyDown",
                "modifiers": modifiers,
                "key": key,
                "code": code,
            }
            
            # Add text parameter for printable characters (letters, numbers, symbols)
            # Skip for special keys like Enter, Escape, Arrow keys, etc.
            if len(key) == 1:  # Simple check for printable characters (single character keys)
                key_params["text"] = key
            
            # For Backspace, also send the 'deleteBackward' editing command
            if key == 'Backspace':
                 send_log("Adding 'deleteBackward' command for Backspace keydown", "🔧", log_type='status')
                 key_params["commands"] = ["deleteBackward"]
            
            try:
                await active_cdp_session.send("Input.dispatchKeyEvent", key_params)
            except Exception as key_error:
                send_log(f"Input error: Failed to send keyDown: {key_error}", "❌", log_type='status')
                return
            
            send_log(f"Key down sent: {key}", "⌨️", log_type='status')

        elif event_type == 'keyup':
            key = details.get('key', '')
            code = details.get('code', '')
            modifiers = _map_modifiers(details)
            
            key_params = {
                "type": "keyUp",
                "modifiers": modifiers,
                "key": key,
                "code": code,
            }
            
            try:
                await active_cdp_session.send("Input.dispatchKeyEvent", key_params)
            except Exception as key_error:
                send_log(f"Input error: Failed to send keyUp: {key_error}", "❌", log_type='status')
                return
            
            send_log(f"Key up sent: {key}", "⌨️", log_type='status')

        elif event_type == 'scroll':
            # Use dispatchMouseEvent with type 'mouseWheel'
            x = details.get('x', 0)
            y = details.get('y', 0)
            delta_x = details.get('deltaX', 0)
            delta_y = details.get('deltaY', 0)
            
            wheel_params = {
                "type": "mouseWheel",
                "x": x,
                "y": y,
                "deltaX": delta_x,
                "deltaY": delta_y,
                "modifiers": 0 # Modifiers usually not needed for scroll
            }
            
            try:
                await active_cdp_session.send("Input.dispatchMouseEvent", wheel_params)
            except Exception as wheel_error:
                send_log(f"Input error: Failed to send mouseWheel: {wheel_error}", "❌", log_type='status')
                return
            
            # send_log(f"Scroll sent: dY={delta_y}", "📜", log_type='status')

        else:
            send_log(f"Unknown input type: {event_type}", "❓", log_type='status')

    except Exception as e:
        send_log(f"Input error: {e}", "❌", log_type='status')
        
        # Check if the session is closed
        if "Target closed" in str(e) or "Session closed" in str(e) or "Connection closed" in str(e):
            send_log("CDP session closed, stopping input handling", "⚠️", log_type='status')
            active_screencast_running = False # Mark as stopped
            if active_cdp_session:
                try: 
                    await active_cdp_session.detach()
                except Exception: 
                    pass
                active_cdp_session = None

def _map_modifiers(details: Dict) -> int:
    """Maps modifier keys from frontend details to CDP modifier bitmask."""
    modifiers = 0
    if details.get('altKey'): modifiers |= 1
    if details.get('ctrlKey'): modifiers |= 2
    if details.get('metaKey'): modifiers |= 4 # Command key on Mac
    if details.get('shiftKey'): modifiers |= 8
    return modifiers

def set_screencast_running(running: bool = True) -> None:
    """Set the active_screencast_running flag.
    
    Args:
        running: Whether the screencast is running
        
    Returns:
        None
    """
    global active_screencast_running
    active_screencast_running = running

# Helper function to get persisted browser state
def _get_persisted_state() -> Optional[str]:
    """
    Check for and return the path to persisted browser state if it exists.
    
    Returns:
        Optional[str]: Path to the state file if it exists, None otherwise
    """
    state_file = os.path.expanduser("~/.operative/browser_state/state.json")
    return state_file if os.path.exists(state_file) else None

async def run_browser_task(task: str, tool_call_id: str = None, api_key: str = None, headless: bool = True) -> Dict[str, Any]:
    global browser_task_loop, screenshot_task
    # Store the current asyncio loop for input handling
    browser_task_loop = asyncio.get_running_loop()
    """
    Run a task using browser-use agent, sending logs to the dashboard.

    Args:
        task: The task to run.
        tool_call_id: The tool call ID for API headers.
        api_key: The API key for authentication.

    Returns:
        str: Agent's final result (stringified).
    """
    global agent_instance, console_log_storage, network_request_storage, screenshot_storage, original_create_context, _original_bring_to_front
    global active_cdp_session, active_screencast_running
    
    # Clear screenshot storage for this run
    screenshot_storage.clear()
    import traceback # Make sure traceback is imported for error logging

    # --- Clear Logs for this Run ---
    console_log_storage.clear()
    network_request_storage.clear()

    # Local Playwright variables for this run
    playwright = None
    playwright_browser = None
    agent_browser = None # browser-use Browser instance
    local_original_create_context = None # To store original method for this run's finally block

    # Configure logging suppression
    logging.basicConfig(level=logging.CRITICAL) # Set root logger level first
    # Then configure specific loggers
    for logger_name in ['browser_use', 'root', 'agent', 'browser']:
        # Get the logger for the current name and set its level
        current_logger = logging.getLogger(logger_name)
        current_logger.setLevel(logging.CRITICAL)

    warnings.filterwarnings("ignore", category=UserWarning)
    set_verbose(False)

    try:
        # Apply the patch to prevent focus stealing
        global _original_bring_to_front
        _original_bring_to_front = PlaywrightPage.bring_to_front
        PlaywrightPage.bring_to_front = _no_bring_to_front
        
        # --- Initialize Playwright Directly ---
        playwright = await async_playwright().start()
        # Launch with CDP enabled
        playwright_browser = await playwright.chromium.launch(
            headless=headless,  # Use the provided headless parameter
            args=["--remote-debugging-port=9222"]
        )
        
        # Get the CDP URL from the browser
        send_log(f"Playwright initialized for task with CDP (headless={headless}).", "🎭", log_type='status') # Type: status

        # --- Check for persisted browser state ---
        persisted_state = _get_persisted_state()
        if persisted_state:
            send_log(f"Loading persisted browser state from {persisted_state}", "💾", log_type='status')
        
        # --- Create browser-use Browser ---
        browser_config = BrowserConfig(disable_security=True, headless=headless, cdp_url="http://127.0.0.1:9222")
        agent_browser = Browser(config=browser_config)
        agent_browser.playwright = playwright
        agent_browser.playwright_browser = playwright_browser
        send_log("Linked Playwright to agent browser with CDP enabled.", "🔗", log_type='status') # Type: status
        
        # --- Set up CDP screencasting ---
        # Detailed logging and error handling for each step
        try:
            # Create a context and page as recommended
            context = await playwright_browser.new_context(
                storage_state=persisted_state
            )
            first_page = await context.new_page()
            
            # Create a CDP session for the page
            try:
                cdp_session = await context.new_cdp_session(first_page)
                # Store the CDP session globally for input handling
                global active_cdp_session
                active_cdp_session = cdp_session
            except Exception as cdp_error:
                send_log(f"Failed to create CDP session: {cdp_error}", "❌", log_type='status')
                import traceback
                raise  # Re-raise to be caught by outer try/except
            
            # Set up a listener for screencast frames
            async def handle_screencast_frame(params):
                if 'data' not in params:
                    return
                    
                if 'sessionId' not in params:
                    return
                
                try:
                    # Format as data URL
                    image_data = params['data']
                    image_data_url = f"data:image/jpeg;base64,{image_data}"
                    
                    # Send to frontend via SocketIO
                    try:
                        from .log_server import send_browser_view
                    except ImportError:
                        return
                    
                    try:
                        await send_browser_view(image_data_url)
                    except Exception:
                        pass
                    
                    # Acknowledge the frame
                    try:
                        await cdp_session.send("Page.screencastFrameAck", {"sessionId": params['sessionId']})
                    except Exception:
                        pass
                except Exception:
                    pass
            
            # Define async wrapper function for screencast frame event
            cdp_session.on("Page.screencastFrame", handle_screencast_frame)
            
            # Start the screencast
            try:
                await cdp_session.send("Page.startScreencast", {
                        "format": "png",
                        "quality": 100,
                        "maxWidth": 1920,
                        "maxHeight": 1080
                    })
            except Exception as start_error:
                send_log(f"Failed to start screencast: {start_error}", "❌", log_type='status')
                import traceback
                raise  # Re-raise to be caught by outer try/except
            
            # Test if we can take a screenshot directly
            try:
                screenshot_bytes = await first_page.screenshot(type='jpeg')
                
                # Try sending this screenshot directly
                import base64
                screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
                direct_image_url = f"data:image/jpeg;base64,{screenshot_b64}"
                
                from .log_server import send_browser_view
                await send_browser_view(direct_image_url)
            except Exception:
                import traceback
            
            send_log("CDP screencast started for browser-use browser.", "📹", log_type='status')
            
            # Define the periodic screenshot capture function
            async def capture_screenshots(page, interval=1/30):
                """Capture screenshots at the specified interval in seconds (30 FPS)."""
                global active_screencast_running
                send_log("Starting periodic screenshot capture at 30 FPS", "🎬", log_type='status')
                try:
                    while active_screencast_running:
                        try:
                            # Take a screenshot
                            screenshot_bytes = await page.screenshot(type='jpeg', quality=80)
                            
                            # Convert to base64
                            screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
                            
                            # Format as data URL
                            screenshot_data_url = f"data:image/jpeg;base64,{screenshot_b64}"
                            
                            # Send to frontend
                            from .log_server import send_browser_view
                            await send_browser_view(screenshot_data_url)
                            
                        except Exception as e:
                            if not active_screencast_running:
                                break
                            # Don't log every error to avoid spamming
                            if "Target closed" in str(e) or "Session closed" in str(e) or "Connection closed" in str(e):
                                active_screencast_running = False
                                break
                        
                        # Wait for the next interval
                        await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    send_log("Periodic screenshot capture stopped", "🛑", log_type='status')
                except Exception as e:
                    send_log(f"Screenshot capture error: {e}", "❌", log_type='status')
            
            # Start the screenshot capture task
            active_screencast_running = True
            if headless:
                screenshot_task = asyncio.create_task(capture_screenshots(first_page))
            
        except Exception as e:
            send_log(f"Failed to start CDP screencast: {e}", "❌", log_type='status')
            import traceback

        # --- Patch BrowserContext._create_context ---
        # Store original only if not already stored (first run)
        if original_create_context is None:
            original_create_context = BrowserContext._create_context
            local_original_create_context = original_create_context # Also store for finally block
        else:
            # Already patched, just ensure we have a reference for finally
            local_original_create_context = original_create_context

        async def patched_create_context(self, browser_pw):
            if original_create_context is None:
                 raise RuntimeError("Original _create_context not stored correctly")

            # Check for persisted browser state
            persisted_state = _get_persisted_state()
            if persisted_state:
                send_log("Loading persisted browser state in new context", "💾", log_type='status')
            
            # Call the original method but with storage_state if available
            raw_playwright_context = await original_create_context(self, browser_pw)
            
            # Apply storage state after context creation if available
            if persisted_state and raw_playwright_context:
                try:
                    with open(persisted_state, 'r') as f:
                        state_data = json.load(f)
                    
                    # Load cookies and localStorage from state
                    if 'cookies' in state_data:
                        await raw_playwright_context.add_cookies(state_data['cookies'])
                    
                    # Origins with storage set is already handled by Playwright internally
                    send_log("Applied persisted browser state to context", "💾", log_type='status')
                except Exception as e:
                    send_log(f"Failed to apply persisted state to context: {e}", "⚠️", log_type='status')

            if raw_playwright_context:
                # Use the non-async wrapper functions for event listeners
                raw_playwright_context.on("console", handle_console_message)
                raw_playwright_context.on("request", handle_request)
                raw_playwright_context.on("requestfailed", handle_request_failed)
                raw_playwright_context.on("response", handle_response)
                raw_playwright_context.on("weberror", handle_web_error)
                raw_playwright_context.on("pageerror", handle_page_error)
                
                # Set up agent controls for existing pages
                for page in raw_playwright_context.pages:
                    await setup_page_agent_controls(page)
                
                # Define non-async wrapper function for page event
                def on_page(page):
                    asyncio.create_task(setup_page_agent_controls(page))
                
                # Set up agent controls for new pages using non-async wrapper
                raw_playwright_context.on("page", on_page)
                
                send_log("Log listeners and agent controls attached.", "👂", log_type='status') # Type: status
            else:
                 send_log("Original _create_context did not return a context.", "⚠️", log_type='status') # Type: status

            return raw_playwright_context

        BrowserContext._create_context = patched_create_context

        # --- Ensure Tool Call ID ---
        if tool_call_id is None:
            tool_call_id = str(uuid.uuid4())
            send_log(f"Generated tool_call_id: {tool_call_id}", "🆔", log_type='status') # Type: status

        # --- LLM Setup ---
        from .env_utils import get_backend_url
        
        llm = ChatAnthropic(model="claude-3-5-sonnet-20240620",
            base_url=get_backend_url("v1beta/models/claude-3-5-sonnet-20240620"),
            extra_headers={
                "x-operative-api-key": api_key,
                "x-operative-tool-call-id": tool_call_id
            })
        send_log(f"LLM ({llm.model}) configured.", "🤖", log_type='status') # Type: status

        # --- Agent Callback ---
        async def state_callback(browser_state, agent_output, step_number):
            global agent_instance, screenshot_storage # Ensure we have access to the agent and screenshot storage

            # Send agent output with type 'agent'
            send_log(f"Step {step_number}", "📍", log_type='agent')
            send_log(f"URL: {browser_state.url}", "🔗", log_type='agent')

            # Capture screenshot at each step
            try:
                if agent_instance and agent_instance.browser_context:
                    # Use the provided helper method to get the current page
                    current_page = await agent_instance.browser_context.get_current_page()

                    if current_page:
                        # Take screenshot
                        screenshot_bytes = await current_page.screenshot(type='jpeg', quality=80)
                        screenshot_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')
                        
                        # Log screenshot size for debugging
                        send_log(f"Screenshot captured: {len(screenshot_bytes)} bytes, {len(screenshot_base64)} base64 chars", "📊", log_type='status')
                        
                        # Store screenshot with metadata
                        screenshot_storage.append({
                            'step': step_number,
                            'url': browser_state.url,
                            'timestamp': asyncio.get_event_loop().time(),
                            'screenshot': screenshot_base64
                        })
                        
                        send_log(f"Screenshot stored in storage (total: {len(screenshot_storage)})", "📸", log_type='status')
                        
                        # Re-inject the overlay
                        send_log(f"Re-injecting overlay after step {step_number} into page {current_page.url}", "🔄", log_type='status')
                    else:
                        send_log(f"Could not get current page from agent context for step {step_number}", "⚠️", log_type='status')
                else:
                     send_log(f"Agent instance or browser context not available for step {step_number}", "⚠️", log_type='status')

            except Exception as e:
                # Add traceback for debugging other potential errors
                import traceback
                tb_str = traceback.format_exc()
                send_log(f"Failed to capture screenshot or re-inject overlay after step: {e}\n{tb_str}", "⚠️", log_type='status')

            # Ensure agent_output is a string before logging
            output_str = str(agent_output)
            send_log(f"Agent Output: {output_str}", "💬", log_type='agent')

        # --- Initialize and Run Agent ---
        agent = Agent(
            task=task,
            llm=llm,
            browser=agent_browser,
            register_new_step_callback=state_callback
        )
        agent_instance = agent

        send_log(f"Agent starting task: {task}", "🏃", log_type='agent') # Type: agent
        agent_result = await agent.run()
        send_log("Agent run finished.", "🏁", log_type='agent') # Type: agent

        # --- Prepare Combined Results ---
        # Convert AgentHistoryList to a serializable format (just stringify)
        serialized_result = str(agent_result)

        # Log information about screenshots before returning
        send_log(f"Returning {len(screenshot_storage)} screenshots from run_browser_task", "📸", log_type='status')
        if screenshot_storage:
            for i, screenshot in enumerate(screenshot_storage):
                send_log(f"Screenshot {i+1}: Step {screenshot['step']}, {len(screenshot['screenshot'])} base64 chars", "🔢", log_type='status')
        else:
            send_log("No screenshots captured during task execution!", "⚠️", log_type='status')

        # Return the agent result and screenshots
        return {
            "result": serialized_result,
            "screenshots": screenshot_storage
        }

    except Exception as e:
        error_message = f"Error in run_browser_task: {e}\n{traceback.format_exc()}"
        send_log(error_message, "❌", log_type='status') # Type: status
        return {
            "result": error_message,
            "screenshots": screenshot_storage
        }
    finally:
        # --- Cleanup ---
        # Cancel the screenshot task if it's running
        if screenshot_task:
            screenshot_task.cancel()
            try:
                await screenshot_task
            except asyncio.CancelledError:
                pass
            screenshot_task = None
            send_log("Periodic screenshot task canceled", "🧹", log_type='status')
        
        # Restore the original bring_to_front method
        if _original_bring_to_front:
            PlaywrightPage.bring_to_front = _original_bring_to_front
            
        # Ensure patch is restored
        if local_original_create_context:
            BrowserContext._create_context = local_original_create_context
            send_log("Original BrowserContext restored.", "🔧", log_type='status') # Type: status

        # Close the browser created specifically for this task
        if agent_browser:
            await agent_browser.close()
            agent_browser = None
            send_log("Agent browser resources cleaned up.", "🧹", log_type='status') # Type: status
        # Close the playwright instance started for this task
        if playwright:
            await playwright.stop()
            playwright = None
            send_log("Playwright instance for task stopped.", "🧹", log_type='status') # Type: status

        # Clear the global instance if it was set
        agent_instance = None
        
        # Clear the browser task loop reference
        browser_task_loop = None
