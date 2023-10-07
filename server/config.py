"""
SyncFiles — Configuration Management
YAML-based config with defaults, validation, and env overrides.
"""

import os
import yaml
from pathlib import Path

DEFAULT_CONFIG = {
    'sync': {
        'source': '',
        'destinations': [],
        'watch': True,
        'interval': 5,
        'chunk_size': 4194304,  # 4MB
        'debounce': 1.0,
    },
    'server': {
        'host': '127.0.0.1',
        'port': 8765,
        'bind_network': False,
        'auto_open_browser': True,
        'session_timeout': 1800,  # 30 min
    },
    'git': {
        'enabled': False,
        'auto_commit': False,
        'auto_push': False,
        'branch': 'main',
        'commit_template': 'sync: {timestamp} — {files_changed} files',
    },
    'log': {
        'file': 'sync.log',
        'max_size': 10485760,  # 10MB
        'level': 'info',
    },
}

ENV_MAP = {
    'SYNCFILES_HOST': ('server', 'host'),
    'SYNCFILES_PORT': ('server', 'port'),
    'SYNCFILES_SOURCE': ('sync', 'source'),
    'SYNCFILES_INTERVAL': ('sync', 'interval'),
    'SYNCFILES_CHUNK_SIZE': ('sync', 'chunk_size'),
}


class Config:
    """Manages YAML configuration with defaults and env overrides."""

    def __init__(self, config_path='config.yaml'):
        self.path = Path(config_path)
        self.data = {}
        self.load()

    def load(self):
        """Load config from file, merge with defaults, apply env overrides."""
        self.data = _deep_copy(DEFAULT_CONFIG)
        if self.path.exists():
            try:
                with open(self.path, 'r') as f:
                    user = yaml.safe_load(f) or {}
                _deep_merge(self.data, user)
            except Exception as e:
                print(f"⚠️  Config load error: {e} — using defaults")
        self._apply_env()

    def _apply_env(self):
        """Override config values from environment variables."""
        for env_key, path in ENV_MAP.items():
            val = os.environ.get(env_key)
            if val is not None:
                section, key = path
                expected = type(self.data[section][key])
                try:
                    if expected == int:
                        val = int(val)
                    elif expected == float:
                        val = float(val)
                    elif expected == bool:
                        val = val.lower() in ('1', 'true', 'yes')
                    self.data[section][key] = val
                except (ValueError, TypeError):
                    pass

    def save(self):
        """Write current config to YAML file."""
        with open(self.path, 'w') as f:
            yaml.dump(self.data, f, default_flow_style=False, sort_keys=False)

    def get(self, section, key=None):
        """Get config value. Returns section dict if key is None."""
        if key is None:
            return self.data.get(section, {})
        return self.data.get(section, {}).get(key)

    def set(self, section, key, value):
        """Set config value and save."""
        if section not in self.data:
            self.data[section] = {}
        self.data[section][key] = value
        self.save()

    def create_default(self):
        """Create a default config file."""
        self.data = _deep_copy(DEFAULT_CONFIG)
        self._apply_env()
        self.save()
        return self.path

    def to_dict(self):
        """Return full config as dict (safe for JSON serialization)."""
        return _deep_copy(self.data)

    def validate(self):
        """Validate config values. Returns list of issues."""
        issues = []
        port = self.get('server', 'port')
        if not isinstance(port, int) or port < 1 or port > 65535:
            issues.append(f"server.port must be 1-65535, got: {port}")
        interval = self.get('sync', 'interval')
        if not isinstance(interval, (int, float)) or interval < 1:
            issues.append(f"sync.interval must be >= 1, got: {interval}")
        chunk = self.get('sync', 'chunk_size')
        if not isinstance(chunk, int) or chunk < 1024:
            issues.append(f"sync.chunk_size must be >= 1024, got: {chunk}")
        source = self.get('sync', 'source')
        if source and not Path(source).exists():
            issues.append(f"sync.source path does not exist: {source}")
        return issues


def _deep_copy(d):
    """Simple deep copy for nested dicts/lists."""
    if isinstance(d, dict):
        return {k: _deep_copy(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_deep_copy(i) for i in d]
    return d


def _deep_merge(base, override):
    """Recursively merge override into base."""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
