feat: SyncFiles v1.6 — complete file sync app, 7334 lines, zero frameworks

Multi-PC file sync with rsync-like delta, Google Drive, Git, conflict
detection, encrypted credentials, web dashboard (Workshop-DIY), TUI,
and bash launcher with compact menu + 23 CLI flags.

## Components (7334 lines total)

sync.sh (711): compact 9-item menu, wizard, doctor, benchmark,
  backup/restore, watch mode, clean, 23 CLI flags
server/ (2599): 12 Python modules — sync engine, chunk delta (SHA256),
  conflict detection (19 scenarios), watcher (watchdog), Google Drive
  API (OAuth2), Git (GitPython), SSH/SFTP (paramiko), encrypted
  credentials (Fernet/PBKDF2), YAML config, websocket server, TUI
client/ (3558): Workshop-DIY dashboard — 9 themes, EN/FR/AR, websocket
  client, file tree, conflict diff, git controls, sync history
tests.py (466): 114 deep tests — base, edge, error, concurrency, stress

## Key Features

- Chunk-level delta sync (4MB SHA256 chunks, only diffs transferred)
- Bidirectional conflict detection (19 tested scenarios)
- Side-by-side diff view for conflict resolution
- Google Drive direct API (no Drive for Desktop)
- Git auto-commit/push/pull
- SSH/SFTP remote sync
- Fernet AES-256 encrypted credentials
- Real-time file watching (watchdog + debounce)
- 3 interfaces: bash menu, web dashboard, curses TUI
- Launcher: wizard, doctor, benchmark, backup/restore, watch, clean
- 114 tests (edge, error, concurrency, stress), 0 failures

## Version History

v1.0 Foundation → v1.1 Dashboard → v1.2 Smart Sync →
v1.3 Integrations → v1.4 TUI → v1.5 Deep Tests →
v1.6 Launcher Rewrite

Workshop-DIY — abourdim
