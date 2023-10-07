"""
SyncFiles — Chunk-Level Delta Sync Engine
SHA256 chunk hashing for efficient large file transfers.
"""

import os
import json
import hashlib
from pathlib import Path

DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024  # 4MB


def compute_file_hash(filepath):
    """Compute SHA256 hash of entire file."""
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while True:
            block = f.read(65536)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def compute_chunk_manifest(filepath, chunk_size=DEFAULT_CHUNK_SIZE):
    """
    Compute chunk manifest for a file.
    Returns: {
        'path': str,
        'total_size': int,
        'chunk_size': int,
        'file_hash': str,
        'chunks': [{'index': int, 'offset': int, 'size': int, 'hash': str}, ...]
    }
    """
    filepath = Path(filepath)
    total_size = filepath.stat().st_size
    chunks = []
    file_hash = hashlib.sha256()

    with open(filepath, 'rb') as f:
        index = 0
        offset = 0
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            file_hash.update(data)
            chunk_hash = hashlib.sha256(data).hexdigest()
            chunks.append({
                'index': index,
                'offset': offset,
                'size': len(data),
                'hash': chunk_hash,
            })
            offset += len(data)
            index += 1

    return {
        'path': str(filepath),
        'total_size': total_size,
        'chunk_size': chunk_size,
        'file_hash': file_hash.hexdigest(),
        'chunks': chunks,
    }


def compute_delta(local_manifest, remote_manifest):
    """
    Compare two chunk manifests.
    Returns list of chunk indices that differ (need transfer).
    """
    if local_manifest['file_hash'] == remote_manifest['file_hash']:
        return []  # Files identical

    local_chunks = {c['index']: c['hash'] for c in local_manifest['chunks']}
    remote_chunks = {c['index']: c['hash'] for c in remote_manifest['chunks']}

    changed = []

    # Chunks that differ or are new in local
    all_indices = set(local_chunks.keys()) | set(remote_chunks.keys())
    for idx in sorted(all_indices):
        local_h = local_chunks.get(idx)
        remote_h = remote_chunks.get(idx)
        if local_h != remote_h:
            changed.append(idx)

    return changed


def extract_chunks(filepath, indices, chunk_size=DEFAULT_CHUNK_SIZE):
    """
    Read specific chunks from a file.
    Returns: {index: bytes, ...}
    """
    result = {}
    with open(filepath, 'rb') as f:
        for idx in indices:
            f.seek(idx * chunk_size)
            data = f.read(chunk_size)
            if data:
                result[idx] = data
    return result


def apply_chunks(filepath, chunks_data, manifest):
    """
    Write chunks to a file, creating or updating it.
    chunks_data: {index: bytes, ...}
    manifest: the target manifest (for total_size and chunk layout)
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file first (atomic)
    tmp = filepath.with_suffix('.sync_tmp')
    try:
        # If file exists, read current content
        if filepath.exists():
            with open(filepath, 'rb') as f:
                current = f.read()
        else:
            current = b''

        # Build new content
        chunk_size = manifest['chunk_size']
        total_chunks = len(manifest['chunks'])
        parts = []

        for i in range(total_chunks):
            if i in chunks_data:
                parts.append(chunks_data[i])
            else:
                # Keep existing chunk
                offset = i * chunk_size
                end = min(offset + chunk_size, len(current))
                if offset < len(current):
                    parts.append(current[offset:end])

        with open(tmp, 'wb') as f:
            for part in parts:
                f.write(part)

        # Atomic rename
        tmp.replace(filepath)
        return True
    except Exception as e:
        if tmp.exists():
            tmp.unlink()
        raise e


def is_small_file(filepath, chunk_size=DEFAULT_CHUNK_SIZE):
    """Check if file is small enough to skip chunking."""
    return Path(filepath).stat().st_size <= chunk_size


class ChunkCache:
    """Cache chunk manifests to avoid recomputation."""

    def __init__(self, cache_path='.sync_state/chunk_cache.json'):
        self.path = Path(cache_path)
        self._cache = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, 'r') as f:
                    self._cache = json.load(f)
            except Exception:
                self._cache = {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump(self._cache, f)

    def get(self, filepath):
        """Get cached manifest if file unchanged (by mtime + size)."""
        filepath = str(filepath)
        if filepath not in self._cache:
            return None
        entry = self._cache[filepath]
        try:
            stat = os.stat(filepath)
            if stat.st_mtime == entry['mtime'] and stat.st_size == entry['size']:
                return entry['manifest']
        except OSError:
            pass
        return None

    def put(self, filepath, manifest):
        """Cache a manifest for a file."""
        filepath_str = str(filepath)
        try:
            stat = os.stat(filepath)
            self._cache[filepath_str] = {
                'mtime': stat.st_mtime,
                'size': stat.st_size,
                'manifest': manifest,
            }
            self._save()
        except OSError:
            pass

    def invalidate(self, filepath):
        """Remove cached manifest."""
        self._cache.pop(str(filepath), None)
        self._save()
