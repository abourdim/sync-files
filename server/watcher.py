"""
SyncFiles — Filesystem Watcher
Watches directories for changes using watchdog, with debouncing and .syncignore support.
"""

import os
import re
import time
import fnmatch
import threading
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class SyncIgnore:
    """Parses and matches .syncignore patterns (gitignore-like syntax)."""

    def __init__(self, base_path='.'):
        self.base = Path(base_path)
        self.patterns = []
        self._load()

    def _load(self):
        """Load patterns from .syncignore file."""
        ignore_file = self.base / '.syncignore'
        if not ignore_file.exists():
            self.patterns = self._default_patterns()
            return
        self.patterns = []
        with open(ignore_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                self.patterns.append(line)

    def _default_patterns(self):
        return [
            '.venv/', '__pycache__/', '*.pyc', '*.pyo',
            'node_modules/', '.git/',
            '.vscode/', '.idea/', '*.swp', '*.swo', '*~',
            '.DS_Store', 'Thumbs.db', 'desktop.ini',
            '.credentials/', '.sync_state/', 'sync.log', '*.pid',
            '*.sync_tmp',
        ]

    def should_ignore(self, rel_path):
        """Check if a relative path should be ignored."""
        rel_str = str(rel_path).replace('\\', '/')
        parts = rel_str.split('/')

        for pattern in self.patterns:
            # Directory pattern (ends with /)
            if pattern.endswith('/'):
                dir_pat = pattern.rstrip('/')
                for part in parts:
                    if fnmatch.fnmatch(part, dir_pat):
                        return True
                if fnmatch.fnmatch(rel_str + '/', pattern):
                    return True
            else:
                # File pattern — match against filename and full path
                filename = parts[-1] if parts else ''
                if fnmatch.fnmatch(filename, pattern):
                    return True
                if fnmatch.fnmatch(rel_str, pattern):
                    return True
        return False

    def reload(self):
        """Reload patterns from file."""
        self._load()


class DebouncedHandler(FileSystemEventHandler):
    """
    Filesystem event handler with debouncing.
    Batches rapid changes (IDE save + lint + format) into single events.
    """

    def __init__(self, callback, sync_ignore, base_path, debounce=1.0):
        super().__init__()
        self.callback = callback
        self.sync_ignore = sync_ignore
        self.base = Path(base_path)
        self.debounce = debounce
        self._pending = {}
        self._lock = threading.Lock()
        self._timer = None

    def _get_rel_path(self, path):
        try:
            return Path(path).resolve().relative_to(self.base.resolve())
        except ValueError:
            return Path(path)

    def _should_process(self, path):
        rel = self._get_rel_path(path)
        return not self.sync_ignore.should_ignore(rel)

    def on_created(self, event):
        if not event.is_directory and self._should_process(event.src_path):
            self._queue_event(event.src_path, 'created')

    def on_modified(self, event):
        if not event.is_directory and self._should_process(event.src_path):
            self._queue_event(event.src_path, 'modified')

    def on_deleted(self, event):
        if not event.is_directory and self._should_process(event.src_path):
            self._queue_event(event.src_path, 'deleted')

    def on_moved(self, event):
        if not event.is_directory:
            if self._should_process(event.src_path):
                self._queue_event(event.src_path, 'deleted')
            if self._should_process(event.dest_path):
                self._queue_event(event.dest_path, 'created')

    def _queue_event(self, path, action):
        with self._lock:
            self._pending[path] = {
                'path': path,
                'rel_path': str(self._get_rel_path(path)),
                'action': action,
                'timestamp': time.time(),
            }
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self._flush)
            self._timer.start()

    def _flush(self):
        with self._lock:
            events = list(self._pending.values())
            self._pending.clear()
        if events:
            self.callback(events)


class FileWatcher:
    """Watches a directory for file changes."""

    def __init__(self, watch_path, callback, sync_ignore=None, debounce=1.0):
        self.watch_path = str(watch_path)
        self.callback = callback
        self.sync_ignore = sync_ignore or SyncIgnore(watch_path)
        self.debounce = debounce
        self._observer = None
        self._running = False

    def start(self):
        """Start watching."""
        if self._running:
            return
        handler = DebouncedHandler(
            self.callback, self.sync_ignore, self.watch_path, self.debounce
        )
        self._observer = Observer()
        self._observer.schedule(handler, self.watch_path, recursive=True)
        self._observer.start()
        self._running = True

    def stop(self):
        """Stop watching."""
        if self._observer and self._running:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._running = False

    def is_running(self):
        return self._running
