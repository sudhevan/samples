#!/usr/bin/env python3
"""
Claude Chat Continuous Watcher
Monitors Claude Code JSONL files and maintains live clean chat logs
"""

import json
import os
import time
from pathlib import Path
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import threading
import signal
import sys

class ClaudeChatWatcher:
    def __init__(self):
        self.projects_dir = Path.home() / ".claude" / "projects"
        self.output_dir = Path.home() / "claude_live_chats"
        self.state_dir = Path.home() / ".claude_watcher"
        
        # Create directories
        self.output_dir.mkdir(exist_ok=True)
        self.state_dir.mkdir(exist_ok=True)
        
        # Track reading positions for each JSONL file
        self.file_positions = {}
        self.load_state()
        
        # Track active conversations
        self.active_files = {}
        
        print(f"📁 Watching: {self.projects_dir}")
        print(f"📝 Output: {self.output_dir}")
        print(f"💾 State: {self.state_dir}")
    
    def load_state(self):
        """Load the last read positions from state file"""
        state_file = self.state_dir / "positions.json"
        if state_file.exists():
            try:
                with open(state_file, 'r') as f:
                    self.file_positions = json.load(f)
                print(f"📋 Loaded state for {len(self.file_positions)} files")
            except Exception as e:
                print(f"⚠️ Error loading state: {e}")
                self.file_positions = {}
        else:
            self.file_positions = {}
    
    def save_state(self):
        """Save current read positions to state file"""
        state_file = self.state_dir / "positions.json"
        try:
            with open(state_file, 'w') as f:
                json.dump(self.file_positions, f, indent=2)
        except Exception as e:
            print(f"⚠️ Error saving state: {e}")
    
    def get_output_filename(self, jsonl_path):
        """Generate output filename from JSONL path"""
        # Extract project and session info
        project_dir = jsonl_path.parent.name
        session_id = jsonl_path.stem
        
        # Decode project path and clean it up
        full_decoded_path = project_dir.replace('-', '/')
        if full_decoded_path.startswith('/'):
            full_decoded_path = full_decoded_path[1:]
        
        # Remove home directory prefix
        path_parts = full_decoded_path.split('/')
        if len(path_parts) >= 2 and path_parts[0] == 'Users':
            path_parts = path_parts[2:]  # Skip 'Users' and username
        
        # Create clean project name
        if path_parts:
            project_name = '_'.join(path_parts)
        else:
            project_name = 'home'
        
        # Clean filename
        safe_project_name = ''.join(c for c in project_name if c.isalnum() or c in '_-')
        
        # Return filename: project_sessionid.txt
        return f"{safe_project_name}_{session_id[:8]}.txt"
    
    def extract_text_from_content(self, content):
        """Extract plain text from content"""
        if isinstance(content, str):
            return content.strip()
        elif isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get('type') == 'text':
                    text_parts.append(item.get('text', ''))
            return ''.join(text_parts).strip()
        return ''
    
    def is_system_noise(self, text):
        """Check if text is system noise to filter out"""
        noise_patterns = [
            'This session is being continued',
            'Caveat: The messages below',
            'command-message',
            'init is analyzing',
            '[Request interrupted',
            'tool_result',
            '<local-command-stdout>',
            '<bash-input>',
            '<bash-output>'
        ]
        return any(pattern in text for pattern in noise_patterns)
    
    def process_new_messages(self, jsonl_path):
        """Process new messages from a JSONL file"""
        jsonl_str = str(jsonl_path)
        
        # Get current file size
        try:
            current_size = jsonl_path.stat().st_size
        except FileNotFoundError:
            return  # File was deleted
        
        # Get last read position
        last_position = self.file_positions.get(jsonl_str, 0)
        
        # If file is smaller than last position, it was truncated/recreated
        if current_size < last_position:
            last_position = 0
            print(f"🔄 File reset detected: {jsonl_path.name}")
        
        # If no new data, return
        if current_size <= last_position:
            return
        
        # Generate output filename
        output_filename = self.get_output_filename(jsonl_path)
        output_path = self.output_dir / output_filename
        
        # Track if this is a new conversation
        is_new_conversation = not output_path.exists()
        
        try:
            # Read new content from last position
            with open(jsonl_path, 'r', encoding='utf-8') as f:
                f.seek(last_position)
                new_content = f.read()
                new_position = f.tell()
            
            # Parse new messages
            new_messages = []
            for line in new_content.split('\n'):
                line = line.strip()
                if not line:
                    continue
                
                try:
                    data = json.loads(line)
                    
                    msg_type = data.get('type')
                    message = data.get('message', {})
                    content = message.get('content', '')
                    role = message.get('role', '')
                    
                    if msg_type == 'user' and role == 'user':
                        text = self.extract_text_from_content(content)
                        if text and not self.is_system_noise(text):
                            new_messages.append(f"👤 Me: {text}")
                    
                    elif msg_type == 'assistant' and role == 'assistant':
                        text = self.extract_text_from_content(content)
                        if text:
                            new_messages.append(f"🤖 Claude: {text}")
                
                except json.JSONDecodeError:
                    continue
            
            # If we have new messages, append them
            if new_messages:
                # Create or append to output file
                mode = 'w' if is_new_conversation else 'a'
                
                with open(output_path, mode, encoding='utf-8') as f:
                    if is_new_conversation:
                        # Write header for new conversation
                        project_path = self.decode_project_path(jsonl_path.parent.name)
                        f.write(f"# Claude Code Live Chat Log\n")
                        f.write(f"# Project: {project_path}\n")
                        f.write(f"# Session: {jsonl_path.stem}\n")
                        f.write(f"# Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                        f.write("=" * 60 + "\n\n")
                    
                    # Append new messages
                    for i, msg in enumerate(new_messages):
                        if not is_new_conversation and i == 0:
                            f.write("\n" + "-" * 40 + "\n\n")
                        
                        f.write(msg + "\n\n")
                        
                        if i < len(new_messages) - 1:
                            f.write("-" * 40 + "\n\n")
                
                # Update position
                self.file_positions[jsonl_str] = new_position
                
                # Log activity
                action = "📝 New conversation" if is_new_conversation else "➕ New messages"
                print(f"{action}: {output_filename} (+{len(new_messages)} messages)")
            
            else:
                # Update position even if no messages (for tool calls, etc.)
                self.file_positions[jsonl_str] = new_position
        
        except Exception as e:
            print(f"❌ Error processing {jsonl_path}: {e}")
    
    def decode_project_path(self, encoded_name):
        """Decode project path for display"""
        decoded = encoded_name.replace('-', '/')
        if decoded.startswith('/'):
            decoded = decoded[1:]
        
        # Remove home directory prefix for display
        path_parts = decoded.split('/')
        if len(path_parts) >= 2 and path_parts[0] == 'Users':
            return '/'.join(path_parts[2:])
        return decoded
    
    def scan_existing_files(self):
        """Initial scan of existing JSONL files"""
        print("🔍 Scanning existing conversations...")
        
        jsonl_files = []
        for project_dir in self.projects_dir.iterdir():
            if project_dir.is_dir():
                for jsonl_file in project_dir.glob("*.jsonl"):
                    if jsonl_file.stat().st_size > 0:  # Skip empty files
                        jsonl_files.append(jsonl_file)
        
        print(f"📋 Found {len(jsonl_files)} conversation files")
        
        for jsonl_file in jsonl_files:
            self.process_new_messages(jsonl_file)
        
        self.save_state()
        print("✅ Initial scan complete")
    
    def start_watching(self):
        """Start watching for file changes"""
        print("👀 Starting file watcher...")
        
        # Initial scan
        self.scan_existing_files()
        
        # Set up file watcher
        event_handler = ClaudeFileHandler(self)
        observer = Observer()
        observer.schedule(event_handler, str(self.projects_dir), recursive=True)
        observer.start()
        
        print("🚀 Claude Chat Watcher is running!")
        print("💡 Your conversations will be automatically saved as clean chat logs")
        print("⏹️  Press Ctrl+C to stop")
        
        try:
            # Periodic state saving
            while True:
                time.sleep(30)  # Save state every 30 seconds
                self.save_state()
        
        except KeyboardInterrupt:
            print("\n🛑 Stopping watcher...")
            observer.stop()
            self.save_state()
            print("💾 State saved")
        
        observer.join()
        print("✅ Claude Chat Watcher stopped")

class ClaudeFileHandler(FileSystemEventHandler):
    """File system event handler for Claude JSONL files"""
    
    def __init__(self, watcher):
        self.watcher = watcher
        self.debounce_time = 1.0  # Wait 1 second before processing
        self.pending_files = {}
        self.timer_lock = threading.Lock()
    
    def on_modified(self, event):
        if event.is_directory:
            return
        
        # Only process .jsonl files
        if not event.src_path.endswith('.jsonl'):
            return
        
        jsonl_path = Path(event.src_path)
        
        # Debounce rapid file changes
        with self.timer_lock:
            # Cancel existing timer for this file
            if jsonl_path in self.pending_files:
                self.pending_files[jsonl_path].cancel()
            
            # Set new timer
            timer = threading.Timer(
                self.debounce_time,
                self.watcher.process_new_messages,
                [jsonl_path]
            )
            self.pending_files[jsonl_path] = timer
            timer.start()

def main():
    print("🤖 Claude Code Live Chat Watcher")
    print("=" * 40)
    
    # Setup signal handler for clean shutdown
    def signal_handler(sig, frame):
        print("\n🛑 Received shutdown signal")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start watcher
    watcher = ClaudeChatWatcher()
    watcher.start_watching()

if __name__ == "__main__":
    main()
