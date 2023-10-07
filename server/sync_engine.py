"""
SyncFiles — Core Sync Engine
Orchestrates bidirectional sync across local, Google Drive, SSH destinations.
"""

import os
import json
import time
import shutil
import logging
from pathlib import Path
from datetime import datetime
from .chunk_hash import compute_file_hash, compute_chunk_manifest, compute_delta, \
    extract_chunks, apply_chunks, is_small_file, ChunkCache
from .conflict import ConflictDetector, get_file_info
from .watcher import SyncIgnore

STATE_FILE = '.sync_state/state.json'
LOG_FORMAT = '%(asctime)s [%(levelname)s] %(message)s'

logger = logging.getLogger('syncfiles')


class SyncState:
    """Tracks per-file sync state."""

    def __init__(self, base_path='.'):
        self.base = Path(base_path)
        self.state_path = self.base / STATE_FILE
        self.files = {}
        self._load()

    def _load(self):
        if self.state_path.exists():
            try:
                with open(self.state_path, 'r') as f:
                    self.files = json.load(f)
            except Exception:
                self.files = {}

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, 'w') as f:
            json.dump(self.files, f, indent=2)

    def get(self, rel_path):
        return self.files.get(rel_path)

    def update(self, rel_path, hash_val, mtime, size, status='synced'):
        self.files[rel_path] = {
            'hash': hash_val,
            'mtime': mtime,
            'size': size,
            'status': status,
            'last_synced': datetime.now().isoformat(),
        }
        self.save()

    def remove(self, rel_path):
        self.files.pop(rel_path, None)
        self.save()

    def list_all(self):
        return dict(self.files)


class SyncEngine:
    """Core sync engine for local-to-local sync with delta and conflict support."""

    def __init__(self, config, event_callback=None):
        """
        config: Config object
        event_callback: fn(event_type, data) — sends events to dashboard
        """
        self.config = config
        self.source = Path(config.get('sync', 'source') or '.')
        self.chunk_size = config.get('sync', 'chunk_size') or 4194304
        self.event_cb = event_callback or (lambda *a: None)

        self.state = SyncState(self.source)
        self.conflicts = ConflictDetector(self.source)
        self.ignore = SyncIgnore(self.source)
        self.chunk_cache = ChunkCache(str(self.source / '.sync_state/chunk_cache.json'))

        self._syncing = False
        self._last_sync = None
        self._stats = {'uploaded': 0, 'downloaded': 0, 'conflicts': 0, 'errors': 0}

    def sync(self, destination):
        """
        Run a full bidirectional sync with a destination.

        destination: dict with 'type', 'path' (for local), etc.
        Returns: sync stats dict
        """
        if self._syncing:
            return {'error': 'Sync already in progress'}

        self._syncing = True
        self._stats = {'uploaded': 0, 'downloaded': 0, 'conflicts': 0, 'errors': 0, 'skipped': 0}
        start_time = time.time()

        try:
            dest_type = destination.get('type', 'local')

            if dest_type == 'local':
                self._sync_local(destination)
            elif dest_type == 'gdrive':
                self.event_cb('log', {'msg': 'Google Drive sync — use gdrive module', 'type': 'info'})
            elif dest_type == 'ssh':
                self.event_cb('log', {'msg': 'SSH sync — use ssh_sync module', 'type': 'info'})
            else:
                self.event_cb('log', {'msg': f'Unknown destination type: {dest_type}', 'type': 'error'})

            duration = round(time.time() - start_time, 2)
            self._stats['duration'] = duration
            self._last_sync = datetime.now().isoformat()
            self._stats['timestamp'] = self._last_sync

            self._save_sync_history(self._stats)

            self.event_cb('sync:complete', {
                **self._stats,
                'timestamp': self._last_sync,
            })

            logger.info(f"Sync complete: {self._stats}")
            return self._stats

        except Exception as e:
            logger.error(f"Sync error: {e}")
            self.event_cb('log', {'msg': f'Sync error: {e}', 'type': 'error'})
            self._stats['errors'] += 1
            return self._stats
        finally:
            self._syncing = False

    def _sync_local(self, destination):
        """Bidirectional sync with a local folder."""
        dest_path = Path(destination['path'])
        dest_name = destination.get('name', str(dest_path))

        if not dest_path.exists():
            dest_path.mkdir(parents=True, exist_ok=True)

        self.event_cb('log', {'msg': f'Syncing with {dest_name}...', 'type': 'info'})

        # Scan source files
        source_files = self._scan_dir(self.source)
        dest_files = self._scan_dir(dest_path)

        all_paths = set(source_files.keys()) | set(dest_files.keys())
        total = len(all_paths)
        done = 0

        for rel_path in sorted(all_paths):
            try:
                done += 1
                percent = int(done / total * 100) if total else 100
                self.event_cb('sync:progress', {'file': rel_path, 'action': 'checking', 'percent': percent})
                local_info = source_files.get(rel_path)
                remote_info = dest_files.get(rel_path)
                last_state = self.state.get(rel_path)

                last_sync_info = None
                if last_state:
                    last_sync_info = {'hash': last_state.get('hash'), 'mtime': last_state.get('mtime')}

                action = self.conflicts.check(rel_path, local_info, remote_info, last_sync_info)

                if action == 'skip':
                    self._stats['skipped'] += 1
                    continue

                elif action == 'upload':
                    self._copy_file(self.source / rel_path, dest_path / rel_path, rel_path, 'tx')
                    self._stats['uploaded'] += 1
                    if local_info:
                        self.state.update(rel_path, local_info['hash'], local_info['mtime'], local_info['size'])

                elif action == 'download':
                    self._copy_file(dest_path / rel_path, self.source / rel_path, rel_path, 'rx')
                    self._stats['downloaded'] += 1
                    new_info = get_file_info(self.source / rel_path)
                    if new_info:
                        self.state.update(rel_path, new_info['hash'], new_info['mtime'], new_info['size'])

                elif action == 'delete_remote':
                    if (dest_path / rel_path).exists():
                        (dest_path / rel_path).unlink()
                    self.state.remove(rel_path)
                    self.event_cb('log', {'msg': f'Deleted remote: {rel_path}', 'type': 'info'})

                elif action == 'delete_local':
                    if (self.source / rel_path).exists():
                        (self.source / rel_path).unlink()
                    self.state.remove(rel_path)
                    self.event_cb('log', {'msg': f'Deleted local: {rel_path}', 'type': 'info'})

                elif action == 'conflict':
                    cid = self.conflicts.register_conflict(rel_path, local_info, remote_info, dest_name)
                    self._stats['conflicts'] += 1
                    self.event_cb('conflict:new', {
                        'id': cid, 'path': rel_path,
                        'local': local_info, 'remote': remote_info,
                    })
                    self.event_cb('log', {'msg': f'⚠️ Conflict: {rel_path}', 'type': 'error'})

            except Exception as e:
                self._stats['errors'] += 1
                logger.error(f"Error syncing {rel_path}: {e}")
                self.event_cb('log', {'msg': f'Error: {rel_path} — {e}', 'type': 'error'})

    def _copy_file(self, src, dst, rel_path, direction):
        """Copy file with delta optimization for large files."""
        dst.parent.mkdir(parents=True, exist_ok=True)

        if is_small_file(src, self.chunk_size):
            # Small file — full copy
            shutil.copy2(src, dst)
        else:
            # Large file — chunk delta
            src_manifest = self.chunk_cache.get(src)
            if not src_manifest:
                src_manifest = compute_chunk_manifest(src, self.chunk_size)
                self.chunk_cache.put(src, src_manifest)

            if dst.exists():
                dst_manifest = compute_chunk_manifest(dst, self.chunk_size)
                changed = compute_delta(src_manifest, dst_manifest)
                if not changed:
                    return  # Already identical
                chunks = extract_chunks(src, changed, self.chunk_size)
                apply_chunks(dst, chunks, src_manifest)
            else:
                shutil.copy2(src, dst)

        label = 'TX' if direction == 'tx' else 'RX'
        self.event_cb('log', {'msg': f'{label}: {rel_path}', 'type': direction})

    def _scan_dir(self, directory):
        """Scan directory, return {rel_path: file_info} respecting .syncignore."""
        files = {}
        directory = Path(directory)
        if not directory.exists():
            return files

        for root, dirs, filenames in os.walk(directory):
            # Filter ignored directories
            rel_root = Path(root).relative_to(directory)
            dirs[:] = [d for d in dirs if not self.ignore.should_ignore(rel_root / d)]

            for fname in filenames:
                rel = str((rel_root / fname)).replace('\\', '/')
                if rel.startswith('./'):
                    rel = rel[2:]
                if self.ignore.should_ignore(rel):
                    continue
                full = Path(root) / fname
                try:
                    files[rel] = get_file_info(full)
                except Exception:
                    pass
        return files

    def get_status(self):
        """Get current sync status."""
        pending = sum(1 for f in self.state.files.values() if f.get('status') == 'pending')
        return {
            'syncing': self._syncing,
            'last_sync': self._last_sync,
            'conflicts': self.conflicts.count(),
            'files_tracked': len(self.state.files),
            'files_pending': pending,
            'stats': self._stats,
        }

    def get_file_tree(self):
        """Get file tree with sync status for dashboard."""
        tree = {}
        for rel_path, state in self.state.files.items():
            tree[rel_path] = {
                'status': state.get('status', 'unknown'),
                'size': state.get('size', 0),
                'last_synced': state.get('last_synced', ''),
            }
        # Add active conflicts
        for conflict in self.conflicts.list_active():
            path = conflict['path']
            if path in tree:
                tree[path]['status'] = 'conflict'
            else:
                tree[path] = {'status': 'conflict', 'size': 0, 'last_synced': ''}
        return tree

    def get_sync_history(self, limit=50):
        """Get persisted sync history."""
        hist_path = self.source / '.sync_state' / 'history.json'
        if hist_path.exists():
            try:
                with open(hist_path, 'r') as f:
                    history = json.load(f)
                return history[-limit:]
            except Exception:
                pass
        return []

    def _save_sync_history(self, stats):
        """Append sync result to history file."""
        hist_path = self.source / '.sync_state' / 'history.json'
        hist_path.parent.mkdir(parents=True, exist_ok=True)
        history = []
        if hist_path.exists():
            try:
                with open(hist_path, 'r') as f:
                    history = json.load(f)
            except Exception:
                history = []
        history.append(stats)
        # Keep last 200 entries
        history = history[-200:]
        with open(hist_path, 'w') as f:
            json.dump(history, f, indent=2)

    def handle_file_events(self, events):
        """Handle events from file watcher."""
        for evt in events:
            rel = evt['rel_path']
            action = evt['action']
            self.event_cb('file:changed', {'path': rel, 'action': action, 'status': 'pending'})
            if action in ('created', 'modified'):
                full = Path(evt['path'])
                if full.exists():
                    info = get_file_info(full)
                    if info:
                        self.state.update(rel, info['hash'], info['mtime'], info['size'], status='pending')
            elif action == 'deleted':
                self.state.update(rel, '', 0, 0, status='deleted')
