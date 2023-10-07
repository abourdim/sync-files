# 🔄 SyncFiles

**Smart file sync for developers who work on multiple PCs.**

rsync-like delta sync · Google Drive · Git · conflict detection · encrypted credentials · web dashboard · TUI · bash launcher

Built on [Workshop-DIY](https://github.com/abourdim) — 9 themes, trilingual (EN/FR/AR), zero frameworks.

---

## Quick Start

```bash
chmod +x sync.sh
./sync.sh              # Interactive menu
./sync.sh --wizard     # Guided 5-step setup
./sync.sh --web        # Web dashboard
./sync.sh --tui        # Terminal UI
./sync.sh --sync       # Sync now
```

---

## What It Does

You work on PC-A, close the lid, sit at PC-B — your files are there, synced, no conflicts. SyncFiles handles the messy parts:

- **Delta sync** — files split into 4MB chunks, SHA256-hashed, only changed chunks transferred
- **Conflict detection** — both sides changed? Backup created, you decide which version to keep
- **Google Drive API** — direct OAuth2 integration, no Drive for Desktop needed
- **Git integration** — auto-commit, push, pull as part of sync workflow
- **SSH/SFTP** — sync to remote machines via paramiko
- **Encrypted credentials** — Fernet AES-256, PBKDF2 key derivation, master password
- **Real-time watching** — watchdog monitors your files, debounced events, .syncignore support

---

## Three Interfaces

### Bash Launcher (`./sync.sh`)

Compact 9-item menu with submenus. All features also accessible as `--flags`.

```
╔══════════════════════════════════════╗
║       🔄 SyncFiles v1.6             ║
╠══════════════════════════════════════╣
║  1) 🧙 Setup wizard                 ║
║  2) 🌐 Web Dashboard                ║
║  3) 🖥️  Terminal UI                  ║
║  4) 🔄 Sync now                     ║
║  5) 📊 Status                       ║
║  6) ⚙️  Settings ►                   ║
║  7) 🔧 Tools ►                      ║
║  8) 📖 Help                         ║
║  0) Exit                             ║
╚══════════════════════════════════════╝
```

**Settings ►** config, credentials, auto-sync, ports, log

**Tools ►** doctor, tests, benchmark, watch mode, backup/restore, export, update, clean

**23 CLI flags:** `--wizard` `--web` `--tui` `--sync` `--status` `--config` `--creds` `--start` `--stop` `--ports` `--log` `--doctor` `--tests` `--benchmark` `--watch` `--backup` `--restore` `--export` `--update` `--clean` `--version` `--help`

### Web Dashboard

Workshop-DIY template: 9 themes, EN/FR/AR with RTL, websocket-powered. Shows sync overview, file tree, conflict resolution (side-by-side diff), git controls, sync history, destinations. Plus all template magic: splash, pixel pet, sound, Konami code, matrix rain, breathing guide, night mode.

### Terminal UI (`--tui`)

Curses-based, 4 panels (Status, Files, Conflicts, Log). `s`=sync `p`=auto-sync `1-4`=panels `q`=quit.

---

## Configuration

```yaml
sync:
  source: '/path/to/project'
  destinations:
    - type: local
      name: backup
      path: '/path/to/backup'
  watch: true
  interval: 5
  chunk_size: 4194304

server:
  host: 127.0.0.1
  port: 8765

git:
  enabled: false
  auto_commit: false
  auto_push: false
```

Env overrides: `SYNCFILES_PORT`, `SYNCFILES_HOST`, `SYNCFILES_SOURCE`, `SYNCFILES_INTERVAL`, `SYNCFILES_CHUNK_SIZE`

---

## Testing

```bash
python3 tests.py        # 114 tests, ~11s
python3 tests.py -v     # verbose
```

| Category | Tests | Covers |
|---|---|---|
| Base | 78 | Config, credentials, chunk hash, conflict (19 scenarios), watcher, sync engine, git, gdrive, ssh, app, frontend |
| Edge | 9 | Empty files, binary, unicode, symlinks, deep nesting, dotfiles, delta |
| Error | 7 | Corrupted state files, missing dest, readonly, unknown type |
| Concurrency | 4 | Double sync, 60 concurrent writes, watcher+sync race, thread-safe creds |
| Stress | 6 | 500 files, 20-level nesting, rapid mods, mixed sizes, 100 dirs |

---

## Project Structure

```
sync-files/
├── sync.sh              711 lines  Bash launcher
├── server/
│   ├── app.py           548        Websocket + HTTP server
│   ├── sync_engine.py   338        Core bidirectional sync
│   ├── tui.py           241        Curses terminal UI
│   ├── gdrive.py        219        Google Drive API
│   ├── chunk_hash.py    212        SHA256 delta sync
│   ├── conflict.py      209        Conflict detection
│   ├── git_sync.py      190        Git operations
│   ├── watcher.py       173        Filesystem watcher
│   ├── ssh_sync.py      162        SSH/SFTP sync
│   ├── credentials.py   157        Encrypted store
│   └── config.py        149        YAML config
├── client/
│   ├── script.js       1898        Template + sync client
│   ├── style.css       1205        9 themes
│   ├── index.html       255        Dashboard
│   ├── sync.css         200        SyncFiles styles
│   └── manifest.json                PWA
├── tests.py             466        114 deep tests
├── requirements.txt
├── .syncignore
└── .gitignore
    Total: 7334 lines, zero frameworks
```

---

## Version History

| Version | What |
|---|---|
| 1.0 | Foundation — bash launcher + 11 Python modules |
| 1.1 | Workshop-DIY web dashboard + websocket |
| 1.2 | Sync progress, conflict diff, persistent history |
| 1.3 | Google Drive/Git/SSH wired into dashboard |
| 1.4 | Curses TUI |
| 1.5 | 114 deep tests + 2 bugfixes |
| 1.6 | Launcher rewrite — compact menu, wizard, doctor, benchmark, backup/restore, watch mode, clean |

---

## License

Workshop-DIY — [abourdim](https://github.com/abourdim)
