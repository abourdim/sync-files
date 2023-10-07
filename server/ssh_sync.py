"""
SyncFiles — SSH/SFTP Remote Sync
Sync files to/from remote machines via paramiko.
"""

import os
import stat
import logging
from pathlib import Path, PurePosixPath

logger = logging.getLogger('syncfiles.ssh')

try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False


class SSHSync:
    """SSH/SFTP file sync."""

    def __init__(self, cred_store):
        self.cred_store = cred_store
        self._client = None
        self._sftp = None

    @staticmethod
    def is_available():
        return HAS_PARAMIKO

    def connect(self, host, port=22, username=None, key_path=None, password=None):
        """Connect to SSH server."""
        if not HAS_PARAMIKO:
            raise RuntimeError("paramiko not installed. pip install paramiko")

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kwargs = {'hostname': host, 'port': port}
        if username:
            kwargs['username'] = username
        if key_path:
            kwargs['key_filename'] = str(key_path)
        elif password:
            kwargs['password'] = password

        self._client.connect(**kwargs)
        self._sftp = self._client.open_sftp()
        logger.info(f"SSH connected to {host}:{port}")
        return True

    def connect_from_creds(self, remote_name):
        """Connect using stored credentials."""
        creds = self.cred_store.get('ssh_' + remote_name)
        if not creds:
            raise RuntimeError(f"No SSH credentials for: {remote_name}")
        return self.connect(
            host=creds.get('host'),
            port=creds.get('port', 22),
            username=creds.get('username'),
            key_path=creds.get('key_path'),
            password=creds.get('password'),
        )

    def disconnect(self):
        """Close connection."""
        if self._sftp:
            self._sftp.close()
        if self._client:
            self._client.close()
        self._sftp = None
        self._client = None

    def upload(self, local_path, remote_path):
        """Upload a file."""
        self._require_sftp()
        local_path = Path(local_path)
        remote_path = str(remote_path)

        # Ensure remote directory exists
        remote_dir = str(PurePosixPath(remote_path).parent)
        self._makedirs(remote_dir)

        self._sftp.put(str(local_path), remote_path)
        logger.info(f"Uploaded: {local_path.name} → {remote_path}")

    def download(self, remote_path, local_path):
        """Download a file."""
        self._require_sftp()
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        tmp = local_path.with_suffix('.ssh_tmp')
        try:
            self._sftp.get(str(remote_path), str(tmp))
            tmp.replace(local_path)
            logger.info(f"Downloaded: {remote_path} → {local_path.name}")
        except Exception:
            if tmp.exists():
                tmp.unlink()
            raise

    def list_dir(self, remote_path):
        """List files in remote directory."""
        self._require_sftp()
        result = []
        try:
            for entry in self._sftp.listdir_attr(str(remote_path)):
                result.append({
                    'name': entry.filename,
                    'size': entry.st_size,
                    'mtime': entry.st_mtime,
                    'is_dir': stat.S_ISDIR(entry.st_mode),
                })
        except FileNotFoundError:
            pass
        return result

    def get_file_info(self, remote_path):
        """Get remote file stat."""
        self._require_sftp()
        try:
            st = self._sftp.stat(str(remote_path))
            return {
                'size': st.st_size,
                'mtime': st.st_mtime,
                'exists': True,
            }
        except FileNotFoundError:
            return {'exists': False}

    def delete(self, remote_path):
        """Delete remote file."""
        self._require_sftp()
        self._sftp.remove(str(remote_path))

    def test_connection(self):
        """Test SSH connection."""
        try:
            self._require_sftp()
            self._sftp.listdir('.')
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def _makedirs(self, remote_dir):
        """Recursively create remote directories."""
        parts = PurePosixPath(remote_dir).parts
        current = ''
        for part in parts:
            current = current + '/' + part if current else part
            if current == '/':
                continue
            try:
                self._sftp.stat(current)
            except FileNotFoundError:
                self._sftp.mkdir(current)

    def _require_sftp(self):
        if not self._sftp:
            raise RuntimeError("Not connected. Call connect() first.")
