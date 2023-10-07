"""
SyncFiles — Conflict Detection & Resolution
Detects bidirectional conflicts, creates backups, tracks resolution history.
"""

import os
import json
import shutil
import time
from pathlib import Path
from datetime import datetime
from .chunk_hash import compute_file_hash

CONFLICTS_FILE = '.sync_state/conflicts.json'
HISTORY_FILE = '.sync_state/conflict_history.json'


class ConflictDetector:
    """Detects and manages sync conflicts."""

    def __init__(self, base_path='.'):
        self.base = Path(base_path)
        self.conflicts_path = self.base / CONFLICTS_FILE
        self.history_path = self.base / HISTORY_FILE
        self.conflicts = {}
        self.history = []
        self._load()

    def _load(self):
        """Load active conflicts and history."""
        if self.conflicts_path.exists():
            try:
                with open(self.conflicts_path, 'r') as f:
                    self.conflicts = json.load(f)
            except Exception:
                self.conflicts = {}
        if self.history_path.exists():
            try:
                with open(self.history_path, 'r') as f:
                    self.history = json.load(f)
            except Exception:
                self.history = []

    def _save(self):
        """Save conflicts and history."""
        self.conflicts_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.conflicts_path, 'w') as f:
            json.dump(self.conflicts, f, indent=2)
        with open(self.history_path, 'w') as f:
            json.dump(self.history, f, indent=2)

    def check(self, rel_path, local_info, remote_info, last_sync_info):
        """
        Check if a file has a conflict.

        Args:
            rel_path: Relative file path
            local_info: {'hash': str, 'mtime': float, 'size': int} or None if deleted
            remote_info: {'hash': str, 'mtime': float, 'size': int} or None if deleted
            last_sync_info: {'hash': str, 'mtime': float} — state at last successful sync

        Returns:
            'conflict' | 'upload' | 'download' | 'delete_remote' | 'delete_local' | 'skip'
        """
        if last_sync_info is None:
            # New file — no previous sync state
            if local_info and not remote_info:
                return 'upload'
            if remote_info and not local_info:
                return 'download'
            if local_info and remote_info:
                if local_info['hash'] == remote_info['hash']:
                    return 'skip'
                return 'conflict'
            return 'skip'

        local_changed = self._has_changed(local_info, last_sync_info)
        remote_changed = self._has_changed(remote_info, last_sync_info)

        # Both changed — conflict
        if local_changed and remote_changed:
            # Both deleted = skip (file gone from both sides)
            if local_info is None and remote_info is None:
                return 'skip'
            if local_info and remote_info and local_info['hash'] == remote_info['hash']:
                return 'skip'  # Changed to same content
            return 'conflict'

        # Only local changed
        if local_changed and not remote_changed:
            if local_info is None:
                return 'delete_remote'
            return 'upload'

        # Only remote changed
        if remote_changed and not local_changed:
            if remote_info is None:
                return 'delete_local'
            return 'download'

        return 'skip'

    def _has_changed(self, current_info, last_sync_info):
        """Check if file changed since last sync."""
        if current_info is None and last_sync_info:
            return True  # Deleted
        if current_info and not last_sync_info:
            return True  # New
        if current_info is None and not last_sync_info:
            return False
        return current_info.get('hash') != last_sync_info.get('hash')

    def register_conflict(self, rel_path, local_info, remote_info, destination_name):
        """
        Register a new conflict.
        Creates backup copies and stores conflict metadata.
        """
        conflict_id = f"{rel_path}:{destination_name}:{int(time.time())}"
        ts = datetime.now().isoformat()

        conflict = {
            'id': conflict_id,
            'path': rel_path,
            'destination': destination_name,
            'timestamp': ts,
            'local': local_info,
            'remote': remote_info,
            'status': 'active',
            'backup_path': None,
        }

        # Create backup of local file
        if local_info:
            src = self.base / rel_path
            if src.exists():
                backup_name = f"{src.stem}.conflict_{int(time.time())}{src.suffix}"
                backup_path = src.parent / backup_name
                shutil.copy2(src, backup_path)
                conflict['backup_path'] = str(backup_path.relative_to(self.base))

        self.conflicts[conflict_id] = conflict
        self._save()
        return conflict_id

    def resolve(self, conflict_id, action):
        """
        Resolve a conflict.

        Args:
            conflict_id: The conflict ID
            action: 'keep_local' | 'keep_remote' | 'keep_both'

        Returns:
            Resolved conflict info or None
        """
        if conflict_id not in self.conflicts:
            return None

        conflict = self.conflicts[conflict_id]
        conflict['status'] = 'resolved'
        conflict['resolution'] = action
        conflict['resolved_at'] = datetime.now().isoformat()

        # Move to history
        self.history.append(conflict)
        del self.conflicts[conflict_id]

        # Clean up backup if keeping local
        if action == 'keep_local' and conflict.get('backup_path'):
            backup = self.base / conflict['backup_path']
            if backup.exists():
                backup.unlink()

        self._save()
        return conflict

    def list_active(self):
        """List all active conflicts."""
        return list(self.conflicts.values())

    def list_history(self, limit=50):
        """List resolved conflicts (most recent first)."""
        return sorted(self.history, key=lambda c: c.get('resolved_at', ''), reverse=True)[:limit]

    def count(self):
        """Count active conflicts."""
        return len(self.conflicts)

    def get(self, conflict_id):
        """Get a specific conflict."""
        return self.conflicts.get(conflict_id)

    def clear_resolved(self):
        """Clear conflict history."""
        self.history = []
        self._save()


def get_file_info(filepath):
    """Get file info dict for conflict checking."""
    filepath = Path(filepath)
    if not filepath.exists():
        return None
    stat = filepath.stat()
    return {
        'hash': compute_file_hash(filepath),
        'mtime': stat.st_mtime,
        'size': stat.st_size,
    }
