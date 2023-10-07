# 📋 SyncFiles — Changelog

## v1.6 — 2026-03-13

### Launcher Rewrite (sync.sh — 711 lines)
- Compact 9-item main menu with Settings ► and Tools ► submenus
- **Setup wizard**: 5-step guided flow (install → source → dest → git → credentials)
- **Doctor**: full health check (Python, deps, config, creds, git, ports, disk, daemons, tests)
- **Watch mode**: live terminal dashboard with auto-refresh status + log tail
- **Benchmark**: times sync cycles, reports files/s throughput
- **Backup/restore**: tar.gz state + config + credentials, numbered restore picker
- **Export config**: portable bundle for new machines
- **Update**: git pull + pip install --upgrade
- **Clean**: selective or full wipe (state/creds/venv/log/backups/everything)
- **Tests**: run test suite from menu
- 23 CLI flags preserved, all features accessible both ways

## v1.5 — 2026-03-13

### Deep Tests (tests.py — 114 tests)
- Base (78), edge cases (9), error handling (7), concurrency (4), stress (6)
- Bugfix: conflict.py — both-sides-deleted returns 'skip'
- Bugfix: config.py — create_default() preserves env overrides

### Docs
- PLAN.md, PROMPT.md, NEW_CONVERSATION.md added to repo

## v1.4 — 2026-03-13

### TUI (server/tui.py)
- Curses terminal UI, 4 panels, color-coded, threaded sync

## v1.3 — 2026-03-13

### Integrations
- Google Drive, Git log/diff, destinations rendered in dashboard
- Connection pills: 3 states (grey/amber/green)
- Enriched credentials status

## v1.2 — 2026-03-13

### Smart Sync
- Sync progress bar, conflict diff view, persistent history, pending count

## v1.1 — 2026-03-13

### Web Dashboard
- Workshop-DIY template (9 themes, EN/FR/AR, all magic features)
- Websocket client, file tree, conflicts, git, history, destinations, login

## v1.0 — 2026-03-13

### Foundation
- Bash launcher (14 options) + 11 Python backend modules
- Sync engine, chunk delta, conflict detection, watcher, gdrive, git, ssh, credentials, config

---

Workshop-DIY — [abourdim](https://github.com/abourdim)
