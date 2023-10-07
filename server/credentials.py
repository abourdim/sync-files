"""
SyncFiles — Encrypted Credential Store
Fernet encryption with PBKDF2 key derivation from master password.
"""

import os
import json
import base64
import getpass
import hashlib
from pathlib import Path
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

CREDS_DIR = '.credentials'
SALT_FILE = 'salt'
STORE_FILE = 'store.enc'
ITERATIONS = 100_000


class CredentialStore:
    """Encrypted credential storage using Fernet + PBKDF2."""

    def __init__(self, base_path='.'):
        self.base = Path(base_path)
        self.creds_dir = self.base / CREDS_DIR
        self.salt_path = self.creds_dir / SALT_FILE
        self.store_path = self.creds_dir / STORE_FILE
        self._fernet = None
        self._data = {}

    def is_initialized(self):
        """Check if credential store exists."""
        return self.salt_path.exists() and self.store_path.exists()

    def initialize(self, master_password):
        """Create new credential store with master password."""
        self.creds_dir.mkdir(exist_ok=True)
        os.chmod(self.creds_dir, 0o700)

        salt = os.urandom(32)
        with open(self.salt_path, 'wb') as f:
            f.write(salt)
        os.chmod(self.salt_path, 0o600)

        self._fernet = self._derive_fernet(master_password, salt)
        self._data = {}
        self._save()
        return True

    def unlock(self, master_password):
        """Unlock store with master password. Returns True on success."""
        if not self.is_initialized():
            return False
        try:
            salt = self.salt_path.read_bytes()
            self._fernet = self._derive_fernet(master_password, salt)
            encrypted = self.store_path.read_bytes()
            decrypted = self._fernet.decrypt(encrypted)
            self._data = json.loads(decrypted.decode('utf-8'))
            return True
        except Exception:
            self._fernet = None
            self._data = {}
            return False

    def is_unlocked(self):
        """Check if store is currently unlocked."""
        return self._fernet is not None

    def set(self, service, key, value):
        """Store a credential. Service groups related keys."""
        self._require_unlocked()
        if service not in self._data:
            self._data[service] = {}
        self._data[service][key] = value
        self._save()

    def get(self, service, key=None):
        """Get credential(s). Returns dict if key is None."""
        self._require_unlocked()
        svc = self._data.get(service, {})
        if key is None:
            return dict(svc)
        return svc.get(key)

    def delete(self, service, key=None):
        """Delete a service or specific key."""
        self._require_unlocked()
        if key is None:
            self._data.pop(service, None)
        elif service in self._data:
            self._data[service].pop(key, None)
        self._save()

    def list_services(self):
        """List stored services (no values)."""
        self._require_unlocked()
        result = {}
        for svc, keys in self._data.items():
            result[svc] = list(keys.keys())
        return result

    def has(self, service, key=None):
        """Check if service/key exists."""
        self._require_unlocked()
        if service not in self._data:
            return False
        if key is None:
            return True
        return key in self._data[service]

    def change_master(self, old_password, new_password):
        """Re-encrypt with new master password."""
        if not self.unlock(old_password):
            return False
        salt = os.urandom(32)
        with open(self.salt_path, 'wb') as f:
            f.write(salt)
        self._fernet = self._derive_fernet(new_password, salt)
        self._save()
        return True

    def _derive_fernet(self, password, salt):
        """Derive Fernet key from password using PBKDF2."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=ITERATIONS,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode('utf-8')))
        return Fernet(key)

    def _save(self):
        """Encrypt and save data."""
        self._require_unlocked()
        plaintext = json.dumps(self._data).encode('utf-8')
        encrypted = self._fernet.encrypt(plaintext)
        with open(self.store_path, 'wb') as f:
            f.write(encrypted)
        os.chmod(self.store_path, 0o600)

    def _require_unlocked(self):
        if not self.is_unlocked():
            raise RuntimeError("Credential store is locked. Call unlock() first.")


def prompt_master_password(confirm=False):
    """Interactive master password prompt."""
    pwd = getpass.getpass("🔑 Master password: ")
    if confirm:
        pwd2 = getpass.getpass("🔑 Confirm password: ")
        if pwd != pwd2:
            raise ValueError("Passwords do not match")
    return pwd
