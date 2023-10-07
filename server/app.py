"""
SyncFiles — Websocket + HTTP Server
Serves web dashboard and handles real-time sync communication.
"""

import os
import sys
import json
import asyncio
import logging
import hashlib
import secrets
import mimetypes
import signal
from pathlib import Path
from datetime import datetime

import websockets
import bcrypt

from .config import Config
from .sync_engine import SyncEngine
from .watcher import FileWatcher, SyncIgnore
from .conflict import ConflictDetector
from .credentials import CredentialStore
from .git_sync import GitSync
from .gdrive import GDriveSync

logger = logging.getLogger('syncfiles')

# Globals
config = None
engine = None
watcher = None
cred_store = None
git_sync = None
gdrive_sync = None
clients = set()
sessions = {}
auto_sync_task = None

CLIENT_DIR = Path(__file__).parent.parent / 'client'


async def handle_http(connection, request):
    """Serve static files for the web dashboard.
    Returns None for WebSocket upgrades so they proceed normally.
    Returns a Response for regular HTTP requests (static files).
    """
    from websockets.http11 import Response
    from websockets.datastructures import Headers

    # Allow WebSocket upgrade requests to pass through
    connection_header = ','.join(request.headers.get_all('Connection')).lower()
    if 'upgrade' in connection_header:
        return None

    path = request.path if hasattr(request, 'path') else str(request)
    if path == '/' or path == '':
        path = '/index.html'

    file_path = CLIENT_DIR / path.lstrip('/')
    if not file_path.exists() or not file_path.is_file():
        return Response(404, 'Not Found', Headers(), b'Not Found\n')

    content_type, _ = mimetypes.guess_type(str(file_path))
    content_type = content_type or 'application/octet-stream'

    data = file_path.read_bytes()
    headers = Headers([
        ('Content-Type', content_type),
        ('Content-Length', str(len(data))),
        ('Cache-Control', 'no-cache'),
    ])
    return Response(200, 'OK', headers, data)


async def handler(websocket):
    """Handle websocket connections."""
    clients.add(websocket)
    authenticated = False
    session_token = None

    try:
        # Check if auth is required
        if cred_store and cred_store.is_unlocked() and cred_store.has('dashboard', 'password_hash'):
            await websocket.send(json.dumps({'type': 'auth:required'}))
        else:
            authenticated = True
            await send_status(websocket)

        async for message in websocket:
            try:
                msg = json.loads(message)
                msg_type = msg.get('type', '')

                # Auth handling
                if msg_type == 'auth:login':
                    result = handle_auth(msg.get('password', ''))
                    if result:
                        authenticated = True
                        session_token = result
                        await websocket.send(json.dumps({'type': 'auth:ok', 'token': result}))
                        await send_status(websocket)
                    else:
                        await websocket.send(json.dumps({'type': 'auth:error', 'msg': 'Invalid password'}))
                    continue

                if not authenticated:
                    await websocket.send(json.dumps({'type': 'auth:required'}))
                    continue

                # Dispatch commands
                response = await dispatch(msg_type, msg)
                if response:
                    await websocket.send(json.dumps(response))

            except json.JSONDecodeError:
                await websocket.send(json.dumps({'type': 'error', 'msg': 'Invalid JSON'}))
            except Exception as e:
                logger.error(f"Handler error: {e}")
                await websocket.send(json.dumps({'type': 'error', 'msg': str(e)}))

    except websockets.ConnectionClosed:
        pass
    finally:
        clients.discard(websocket)


def handle_auth(password):
    """Verify dashboard password. Returns session token or None."""
    if not cred_store or not cred_store.is_unlocked():
        return secrets.token_hex(32)

    stored_hash = cred_store.get('dashboard', 'password_hash')
    if not stored_hash:
        return secrets.token_hex(32)

    if bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8')):
        token = secrets.token_hex(32)
        sessions[token] = datetime.now()
        return token
    return None


async def dispatch(msg_type, msg):
    """Route websocket commands to handlers."""
    global auto_sync_task

    if msg_type == 'status:get':
        return {'type': 'status', 'data': get_full_status()}

    elif msg_type == 'sync:manual':
        asyncio.create_task(run_sync())
        return {'type': 'log:entry', 'data': {'msg': '🔄 Manual sync started...', 'type': 'info', 'timestamp': _ts()}}

    elif msg_type == 'sync:start':
        if auto_sync_task is None or auto_sync_task.done():
            auto_sync_task = asyncio.create_task(auto_sync_loop())
            return {'type': 'log:entry', 'data': {'msg': '▶️ Auto-sync started', 'type': 'success', 'timestamp': _ts()}}
        return {'type': 'log:entry', 'data': {'msg': 'Auto-sync already running', 'type': 'info', 'timestamp': _ts()}}

    elif msg_type == 'sync:stop':
        if auto_sync_task and not auto_sync_task.done():
            auto_sync_task.cancel()
            auto_sync_task = None
            return {'type': 'log:entry', 'data': {'msg': '⏹ Auto-sync stopped', 'type': 'info', 'timestamp': _ts()}}
        return {'type': 'log:entry', 'data': {'msg': 'Auto-sync not running', 'type': 'info', 'timestamp': _ts()}}

    elif msg_type == 'files:tree':
        return {'type': 'files:tree', 'data': engine.get_file_tree() if engine else {}}

    elif msg_type == 'conflicts:list':
        conflicts = engine.conflicts.list_active() if engine else []
        return {'type': 'conflicts:list', 'data': conflicts}

    elif msg_type == 'conflicts:resolve':
        cid = msg.get('id')
        action = msg.get('action', 'keep_local')
        if engine:
            result = engine.conflicts.resolve(cid, action)
            if result:
                await broadcast({'type': 'log:entry', 'data': {'msg': f'✅ Conflict resolved: {result["path"]} → {action}', 'type': 'success', 'timestamp': _ts()}})
            return {'type': 'conflicts:resolved', 'data': result}

    elif msg_type == 'conflicts:history':
        history = engine.conflicts.list_history() if engine else []
        return {'type': 'conflicts:history', 'data': history}

    elif msg_type == 'sync:history':
        history = engine.get_sync_history() if engine else []
        return {'type': 'sync:history', 'data': history}

    elif msg_type == 'conflict:diff':
        # Return text content of both sides for diff view
        cid = msg.get('id')
        if engine:
            conflict = engine.conflicts.get(cid)
            if conflict:
                diff_data = _get_conflict_diff(conflict)
                return {'type': 'conflict:diff', 'data': diff_data}

    elif msg_type == 'config:get':
        return {'type': 'config', 'data': config.to_dict() if config else {}}

    elif msg_type == 'config:set':
        key = msg.get('key', '')
        value = msg.get('value')
        if '.' in key:
            section, k = key.split('.', 1)
            config.set(section, k, value)
            return {'type': 'log:entry', 'data': {'msg': f'⚙️ Config updated: {key}', 'type': 'info', 'timestamp': _ts()}}

    elif msg_type == 'log:get':
        limit = msg.get('limit', 100)
        return {'type': 'log:history', 'data': get_log_tail(limit)}

    elif msg_type == 'creds:status':
        return {'type': 'creds:status', 'data': get_creds_status()}

    elif msg_type == 'git:status':
        if git_sync:
            try:
                return {'type': 'git:status', 'data': git_sync.status()}
            except Exception as e:
                return {'type': 'git:status', 'data': {'error': str(e)}}

    elif msg_type == 'git:commit':
        if git_sync:
            try:
                result = git_sync.commit(msg.get('message'))
                if result:
                    await broadcast({'type': 'log:entry', 'data': {'msg': f'📝 Git commit: {result["sha"]}', 'type': 'success', 'timestamp': _ts()}})
                return {'type': 'git:commit', 'data': result}
            except Exception as e:
                return {'type': 'git:error', 'data': {'error': str(e)}}

    elif msg_type == 'git:push':
        if git_sync:
            try:
                result = git_sync.push()
                await broadcast({'type': 'log:entry', 'data': {'msg': f'📤 Git push: {result["branch"]}', 'type': 'tx', 'timestamp': _ts()}})
                return {'type': 'git:push', 'data': result}
            except Exception as e:
                return {'type': 'git:error', 'data': {'error': str(e)}}

    elif msg_type == 'git:pull':
        if git_sync:
            try:
                result = git_sync.pull()
                await broadcast({'type': 'log:entry', 'data': {'msg': f'📥 Git pull: {result["branch"]}', 'type': 'rx', 'timestamp': _ts()}})
                return {'type': 'git:pull', 'data': result}
            except Exception as e:
                return {'type': 'git:error', 'data': {'error': str(e)}}

    elif msg_type == 'git:log':
        if git_sync:
            try:
                return {'type': 'git:log', 'data': git_sync.log(msg.get('limit', 10))}
            except Exception as e:
                return {'type': 'git:error', 'data': {'error': str(e)}}

    elif msg_type == 'git:diff':
        if git_sync:
            try:
                return {'type': 'git:diff', 'data': {'diff': git_sync.diff(msg.get('path'))}}
            except Exception as e:
                return {'type': 'git:error', 'data': {'error': str(e)}}

    elif msg_type == 'gdrive:status':
        if gdrive_sync:
            result = gdrive_sync.test_connection()
            return {'type': 'gdrive:status', 'data': result}
        return {'type': 'gdrive:status', 'data': {'ok': False, 'error': 'Not configured'}}

    elif msg_type == 'gdrive:list':
        if gdrive_sync:
            try:
                files = gdrive_sync.list_files(msg.get('folder_id'))
                return {'type': 'gdrive:list', 'data': files}
            except Exception as e:
                return {'type': 'gdrive:error', 'data': {'error': str(e)}}

    elif msg_type == 'dest:list':
        dests = config.get('sync', 'destinations') or []
        dest_info = []
        for d in dests:
            info = {**d, 'status': 'ok'}
            if d.get('type') == 'local':
                from pathlib import Path as P
                info['status'] = 'ok' if P(d.get('path', '')).exists() else 'error'
            dest_info.append(info)
        return {'type': 'dest:list', 'data': dest_info}

    elif msg_type == 'dest:test':
        dest = msg.get('destination', {})
        dtype = dest.get('type', 'local')
        if dtype == 'local':
            from pathlib import Path as P
            exists = P(dest.get('path', '')).exists()
            return {'type': 'dest:test', 'data': {'ok': exists, 'type': dtype}}
        elif dtype == 'gdrive' and gdrive_sync:
            result = gdrive_sync.test_connection()
            return {'type': 'dest:test', 'data': result}
        elif dtype == 'ssh':
            return {'type': 'dest:test', 'data': {'ok': False, 'error': 'SSH test via dashboard not yet supported. Use ./sync.sh --creds'}}
        return {'type': 'dest:test', 'data': {'ok': False, 'error': 'Unknown type'}}

    return None


async def run_sync():
    """Run a sync cycle for all configured destinations."""
    if not engine:
        return
    destinations = config.get('sync', 'destinations') or []
    if not destinations:
        await broadcast({'type': 'log:entry', 'data': {'msg': 'No destinations configured', 'type': 'error', 'timestamp': _ts()}})
        return

    for dest in destinations:
        stats = engine.sync(dest)

    # Git auto-sync after file sync
    if git_sync and config.get('git', 'enabled'):
        git_sync.auto_sync()

    await broadcast({'type': 'status', 'data': get_full_status()})


async def auto_sync_loop():
    """Background auto-sync loop."""
    interval = config.get('sync', 'interval') or 5
    while True:
        await run_sync()
        await asyncio.sleep(interval)


def get_full_status():
    """Get complete status for dashboard."""
    status = {
        'syncing': engine._syncing if engine else False,
        'last_sync': engine._last_sync if engine else None,
        'auto_sync': auto_sync_task is not None and not auto_sync_task.done() if auto_sync_task else False,
        'conflicts': engine.conflicts.count() if engine else 0,
        'files_tracked': len(engine.state.files) if engine else 0,
        'files_pending': sum(1 for f in engine.state.files.values() if f.get('status') == 'pending') if engine else 0,
        'stats': engine._stats if engine else {},
        'connections': get_creds_status(),
        'watcher_running': watcher.is_running() if watcher else False,
    }
    return status


def _get_conflict_diff(conflict):
    """Read file contents for diff view (text files only)."""
    result = {'id': conflict['id'], 'path': conflict['path'], 'local': None, 'backup': None}
    try:
        source = config.get('sync', 'source') or '.'
        local_path = Path(source) / conflict['path']
        if local_path.exists() and local_path.stat().st_size < 1048576:  # <1MB
            try:
                result['local'] = local_path.read_text(errors='replace')
            except Exception:
                result['local'] = '(binary file)'
        if conflict.get('backup_path'):
            backup = Path(source) / conflict['backup_path']
            if backup.exists() and backup.stat().st_size < 1048576:
                try:
                    result['backup'] = backup.read_text(errors='replace')
                except Exception:
                    result['backup'] = '(binary file)'
    except Exception as e:
        logger.error(f"Diff error: {e}")
    return result


def get_creds_status():
    """Get connection status for all services."""
    status = {
        'gdrive': {'configured': False, 'connected': False, 'user': ''},
        'git': {'configured': False, 'connected': False, 'branch': '', 'dirty': False, 'remote': ''},
        'ssh': {'configured': False, 'connected': False},
        'store': {'initialized': False, 'unlocked': False},
    }
    if cred_store:
        status['store']['initialized'] = cred_store.is_initialized()
        status['store']['unlocked'] = cred_store.is_unlocked()
    if gdrive_sync:
        try:
            status['gdrive']['configured'] = gdrive_sync.is_configured()
            if gdrive_sync.is_configured():
                r = gdrive_sync.test_connection()
                status['gdrive']['connected'] = r.get('ok', False)
                status['gdrive']['user'] = r.get('user', '')
        except Exception:
            pass
    if git_sync:
        status['git']['configured'] = git_sync.is_repo()
        if git_sync.is_repo():
            try:
                s = git_sync.status()
                status['git']['connected'] = s.get('has_remote', False)
                status['git']['branch'] = s.get('branch', '')
                status['git']['dirty'] = s.get('is_dirty', False)
                if s.get('has_remote'):
                    r = git_sync.test_connection()
                    status['git']['remote'] = r.get('url', '')
            except Exception:
                pass
    # SSH — check if any ssh credentials stored
    if cred_store and cred_store.is_unlocked():
        services = cred_store.list_services()
        ssh_keys = [k for k in services if k.startswith('ssh_')]
        status['ssh']['configured'] = len(ssh_keys) > 0
    return status


def get_log_tail(limit=100):
    """Read last N lines from sync log."""
    log_file = config.get('log', 'file') if config else 'sync.log'
    try:
        with open(log_file, 'r') as f:
            lines = f.readlines()
            return [l.strip() for l in lines[-limit:]]
    except FileNotFoundError:
        return []


async def broadcast(message):
    """Send message to all connected clients."""
    if clients:
        data = json.dumps(message)
        await asyncio.gather(*[c.send(data) for c in clients], return_exceptions=True)


async def send_status(websocket):
    """Send initial status to a newly connected client."""
    await websocket.send(json.dumps({'type': 'status', 'data': get_full_status()}))


def event_callback(event_type, data):
    """Sync engine event callback — bridges to websocket broadcast."""
    msg = {'type': event_type, 'data': data}
    for client in list(clients):
        try:
            asyncio.get_event_loop().create_task(client.send(json.dumps(msg)))
        except Exception:
            pass


def _ts():
    return datetime.now().isoformat()


def setup_logging(log_file='sync.log', level='info'):
    """Configure logging."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ]
    )


async def main(config_path='config.yaml', master_password=None):
    """Start the SyncFiles server."""
    global config, engine, watcher, cred_store, git_sync, gdrive_sync

    # Load config
    config = Config(config_path)
    issues = config.validate()
    if issues:
        for issue in issues:
            logger.warning(f"Config issue: {issue}")

    setup_logging(
        config.get('log', 'file') or 'sync.log',
        config.get('log', 'level') or 'info',
    )
    logger.info("SyncFiles server starting...")

    # Credential store
    source = config.get('sync', 'source') or '.'
    cred_store = CredentialStore(source)
    if cred_store.is_initialized():
        # Try provided password, then empty password as fallback
        pwd = master_password if master_password is not None else ''
        if cred_store.unlock(pwd):
            logger.info("Credential store unlocked")
        elif master_password is not None:
            logger.error("Failed to unlock credential store")
        else:
            logger.warning("Credential store locked (no password provided)")

    # Sync engine
    engine = SyncEngine(config, event_callback=event_callback)

    # File watcher
    if config.get('sync', 'source') and config.get('sync', 'watch'):
        sync_ignore = SyncIgnore(source)
        watcher = FileWatcher(
            source, engine.handle_file_events,
            sync_ignore=sync_ignore,
            debounce=config.get('sync', 'debounce') or 1.0,
        )
        watcher.start()
        logger.info(f"File watcher started on: {source}")

    # Git
    git_sync = GitSync(source, config)
    if git_sync.is_repo():
        logger.info("Git repository detected")

    # Google Drive
    gdrive_sync = GDriveSync(cred_store)

    # Start server
    host = config.get('server', 'host') or '127.0.0.1'
    port = config.get('server', 'port') or 8765

    if host == '0.0.0.0':
        logger.warning("⚠️  Dashboard bound to 0.0.0.0 — accessible from network!")

    async with websockets.serve(
        handler,
        host, port,
        process_request=handle_http,
        max_size=50 * 1024 * 1024,  # 50MB max message
    ):
        logger.info(f"🔄 SyncFiles running at http://{host}:{port}")
        logger.info("Press Ctrl+C to stop")

        # Keep running
        stop = asyncio.Future()
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set_result, None)
            except NotImplementedError:
                pass  # Windows

        try:
            await stop
        except asyncio.CancelledError:
            pass

    # Cleanup
    if watcher:
        watcher.stop()
    logger.info("SyncFiles stopped")


def run(config_path='config.yaml', master_password=None):
    """Entry point for running the server."""
    asyncio.run(main(config_path, master_password))


if __name__ == '__main__':
    config_path = sys.argv[1] if len(sys.argv) > 1 else 'config.yaml'
    run(config_path)
