#!/usr/bin/env python3

import asyncio
import threading
import webbrowser
from flask import Flask, render_template, send_from_directory, request
from flask_socketio import SocketIO
import logging
import os
from datetime import datetime
import sys

# Track active dashboard tabs
active_dashboard_tabs = {}
last_tab_activity = {}

# Store current URL and task information
current_url = ""
current_task = ""

# --- Async mode selection ---
_async_mode = 'threading'

# Configure logging for Flask and SocketIO (optional, can be noisy)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR) # Reduce Flask's default logging
logging.getLogger('socketio').setLevel(logging.ERROR)
logging.getLogger('engineio').setLevel(logging.ERROR)

# Get the absolute path to the templates directory
templates_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../templates'))
app = Flask(__name__, template_folder=templates_dir, static_folder=os.path.join(templates_dir, 'static'))
app.config['SECRET_KEY'] = 'secret!' # Replace with a proper secret if needed

# Initialise SocketIO with chosen async_mode
socketio = SocketIO(app, cors_allowed_origins="*", async_mode=_async_mode)

# Store connected SIDs
connected_clients = set()

@app.route('/')
def index():
    """Serve the main HTML dashboard page."""
    return render_template('static/index.html')

@app.route('/static/<path:path>')
def send_static(path):
    """Serve static files (like CSS, JS if added later)."""
    static_folder = os.path.join(os.path.dirname(__file__), '../templates/static')
    return send_from_directory(static_folder, path)

@app.route('/get_url_task')
def get_url_task():
    """Return the current URL and task as JSON."""
    return {'url': current_url, 'task': current_task}

# Dashboard tab tracking handlers
@socketio.on('register_dashboard_tab')
def handle_register_tab(data):
    """Register an active dashboard tab."""
    tab_id = data.get('tabId')
    if tab_id:
        active_dashboard_tabs[tab_id] = request.sid
        last_tab_activity[tab_id] = datetime.now()
        send_log(f"Dashboard tab registered: {tab_id[:8]}...", "📋", log_type='status')

@socketio.on('dashboard_ping')
def handle_dashboard_ping(data):
    """Update last activity time for a dashboard tab."""
    tab_id = data.get('tabId')
    if tab_id and tab_id in active_dashboard_tabs:
        last_tab_activity[tab_id] = datetime.now()

@socketio.on('dashboard_visible')
def handle_dashboard_visible(data):
    """Mark a dashboard tab as currently visible."""
    tab_id = data.get('tabId')
    if tab_id and tab_id in active_dashboard_tabs:
        # This tab is now the most recently active
        last_tab_activity[tab_id] = datetime.now()

@socketio.on('connect')
def handle_connect():
    # Add client to connected_clients set
    connected_clients.add(request.sid)
    
    # Send status message to dashboard
    send_log(f"Connected to log server at {datetime.now().strftime('%H:%M:%S')}", "✅", log_type='status')

@socketio.on('disconnect')
def handle_disconnect():
    # Remove client from connected_clients set
    if request.sid in connected_clients:
        connected_clients.remove(request.sid)
    
    # Remove any dashboard tabs associated with this session
    tabs_to_remove = []
    for tab_id, tab_sid in active_dashboard_tabs.items():
        if tab_sid == request.sid:
            tabs_to_remove.append(tab_id)
    
    for tab_id in tabs_to_remove:
        active_dashboard_tabs.pop(tab_id, None)
        last_tab_activity.pop(tab_id, None)
    
    # Send status message to dashboard
    # Use try-except as send_log might fail if server isn't fully ready/shutting down
    try:
        send_log(f"Disconnected from log server at {datetime.now().strftime('%H:%M:%S')}", "❌", log_type='status')
    except Exception:
        pass

def set_url_and_task(url: str, task: str):
    """Sets the current URL and task and broadcasts it to all connected clients."""
    global current_url, current_task
    current_url = url
    current_task = task

def send_log(message: str, emoji: str = "➡️", log_type: str = 'agent'):
    """Send a log message with an emoji prefix and type to all connected clients."""
    # Ensure socketio context is available. If called from a non-SocketIO thread,
    # use socketio.emit directly.
    try:
        log_entry = f"{emoji} {message}"
        # Include log_type in the emitted data
        socketio.emit('log_message', {'data': log_entry, 'type': log_type})
    except Exception:
        pass

# --- Browser View Update Function ---
async def send_browser_view(image_data_url: str):
    """Sends the browser view image data URL to all connected clients."""
    # This function is async because it might be called from the asyncio loop
    # in browser_manager. However, socketio.emit needs to be called carefully
    # when interacting between asyncio and other threads (like Flask's).
    # socketio.emit is generally thread-safe, but ensure the event loop is handled.
    
    # Check if the data URL is valid
    if not image_data_url or not image_data_url.startswith("data:image/"):
        return
    
    # Mark the screencast as running when we receive a browser view update
    try:
        from .browser_utils import set_screencast_running
        set_screencast_running(True)
    except ImportError:
        pass
    except Exception:
        pass
        
    try:
        socketio.emit('browser_update', {'data': image_data_url})
    except Exception:
        pass

# --- Agent Control Handler ---
@socketio.on('agent_control')
def handle_agent_control(data):
    """Handles agent control events received from the frontend."""
    action = data.get('action')
    
    # Log to the dashboard
    send_log(f"Agent control: {action}", "🤖", log_type='status')
    
    # Import browser_utils to access the agent_instance
    try:
        from .browser_utils import agent_instance
    except ImportError:
        error_msg = "Could not import agent_instance from browser_utils"
        send_log(f"Agent control error: {error_msg}", "❌", log_type='status')
        return
    
    if not agent_instance:
        error_msg = "No active agent instance"
        send_log(f"Agent control error: {error_msg}", "❌", log_type='status')
        return
    
    try:
        if action == 'pause':
            agent_instance.pause()
            send_log("Agent paused", "⏸️", log_type='status')
            # Send updated state
            socketio.emit('agent_state', {'state': {'paused': True, 'stopped': False}})
            
        elif action == 'resume':
            agent_instance.resume()
            send_log("Agent resumed", "▶️", log_type='status')
            # Send updated state
            socketio.emit('agent_state', {'state': {'paused': False, 'stopped': False}})
            
        elif action == 'stop':
            agent_instance.stop()
            send_log("Agent stopped", "⏹️", log_type='status')
            # Send updated state
            socketio.emit('agent_state', {'state': {'paused': False, 'stopped': True}})
            
        else:
            error_msg = f"Unknown agent control action: {action}"
            send_log(f"Agent control error: {error_msg}", "❓", log_type='status')
            
    except Exception as e:
        error_msg = f"Error controlling agent: {e}"
        send_log(f"Agent control error: {error_msg}", "❌", log_type='status')

# --- Browser Input Handler ---
@socketio.on('browser_input')
def handle_browser_input_event(data):
    """Handles browser interaction events received from the frontend."""
    event_type = data.get('type')
    details = data.get('details')
    
    # Log to the dashboard as well
    if event_type != 'scroll':
        send_log(f"Received browser input: {event_type}", "🖱️", log_type='status')
    
    # Import the handle_browser_input function and other utilities from browser_utils
    try:
        from .browser_utils import handle_browser_input, active_cdp_session, active_screencast_running, get_browser_task_loop
    except ImportError:
        error_msg = "Could not import handle_browser_input from browser_utils"
        send_log(f"Input error: {error_msg}", "❌", log_type='status')
        return
    
    # Check if we have an active CDP session
    if not active_cdp_session:
        error_msg = "No active CDP session for input handling"
        send_log(f"Input error: {error_msg}", "❌", log_type='status')
        return
    
    # Since the browser runs in an asyncio loop, and this handler
    # likely runs in a separate thread (Flask/SocketIO default), we need
    # to schedule the async input handler function in the main loop.
    try:
        # Get the browser task loop from browser_utils
        loop = get_browser_task_loop()
        
        if loop is None:
            send_log("Input error: Browser task loop not available", "❌", log_type='status')
            return
        
        # send_log(f"Scheduling {event_type} input handler in browser task loop", "🔄", log_type='status')
        # Schedule the coroutine call
        task = asyncio.run_coroutine_threadsafe(
            handle_browser_input(event_type, details),
            loop
        )
        if event_type == 'scroll':
            return 
        send_log(f"Input {event_type} scheduled for processing", "✅", log_type='status')
        
    except RuntimeError as e:
        error_msg = f"No running asyncio event loop found: {e}"
        send_log(f"Input error: {error_msg}", "❌", log_type='status')
    except Exception as e:
        error_msg = f"Error scheduling browser input handler: {e}"
        send_log(f"Input error: {error_msg}", "❌", log_type='status')


def start_log_server(host='127.0.0.1', port=5009):
    """Starts the Flask-SocketIO server in a background thread."""
    def run_server():
        # Use eventlet or gevent for production? For local dev, default Flask dev server is fine.
        # Setting log_output=False to reduce console noise from SocketIO itself
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')
        socketio.run(app, host=host, port=port, log_output=False, use_reloader=False, allow_unsafe_werkzeug=True)

    # Check if templates directory exists
    template_dir = os.path.join(os.path.dirname(__file__), '../templates')
    static_dir = os.path.join(template_dir, 'static')
    
    # Create template directory if it doesn't exist
    if not os.path.exists(template_dir):
        os.makedirs(template_dir)
    
    # Create static directory if it doesn't exist
    if not os.path.exists(static_dir):
        os.makedirs(static_dir)
    
    # Create index.html if it's missing
    index_path = os.path.join(template_dir, 'index.html')

    # Start the server in a separate thread.
    # run_server uses host/port from the outer scope, so no args needed here.
    server_thread = threading.Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()
    
    # Send initial status message
    send_log("Log server thread started.", "🚀", log_type='status')

def has_active_dashboard():
    """Check if there are any active dashboard tabs."""
    # Clean up stale tabs (inactive for more than 30 seconds)
    now = datetime.now()
    stale_tabs = []
    for tab_id, last_activity in last_tab_activity.items():
        if (now - last_activity).total_seconds() > 30:
            stale_tabs.append(tab_id)
    
    for tab_id in stale_tabs:
        active_dashboard_tabs.pop(tab_id, None)
        last_tab_activity.pop(tab_id, None)
    
    return len(active_dashboard_tabs) > 0

def refresh_dashboard():
    """Send refresh signal to all connected dashboard tabs."""
    if active_dashboard_tabs:
        socketio.emit('refresh_dashboard', {})
        return True
    return False

def open_log_dashboard(url='http://127.0.0.1:5009'):
    """Opens or refreshes the dashboard in the browser."""
    # Try to refresh existing tabs first
    if refresh_dashboard():
        try:
            send_log("Refreshed existing dashboard tab.", "🔄", log_type='status')
        except Exception:
            pass
        return
    
    # No active tabs, open a new one
    try:
        # Use open_new_tab for better control
        webbrowser.open_new_tab(url)
        try:
            send_log(f"Opened new dashboard in browser at {url}.", "🌐", log_type='status')
        except Exception:
            pass
    except Exception as e:
        try:
            send_log(f"Could not open browser automatically: {e}", "⚠️", log_type='status')
        except Exception:
            pass

# Example usage (for testing this module directly)
if __name__ == "__main__":
    start_log_server(port=5009)  # Use a different port
    import time
    time.sleep(2)
    open_log_dashboard(url='http://127.0.0.1:5009')
    set_url_and_task("https://www.example.com", "Test the URL and task display")
    # Use the new log_type argument
    send_log("Server started and dashboard opened.", "✅", log_type='status')
    time.sleep(1)
    send_log("This is a test agent log message.", "🧪", log_type='agent')
    time.sleep(1)
    send_log("This is a test console log.", "🖥️", log_type='console')
    time.sleep(1)
    send_log("This is a test network request.", "➡️", log_type='network')
    time.sleep(1)
    send_log("This is a test network response.", "⬅️", log_type='network')
    # Keep the main thread alive to let the server run
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
