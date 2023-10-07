# 📐 SyncFiles — Architecture

## Modules

| Module | Lines | Purpose |
|---|---|---|
| `sync.sh` | 711 | Bash launcher, 9-item menu + submenus, 23 CLI flags |
| `server/app.py` | 548 | asyncio websocket + HTTP server, auth, dispatch |
| `server/sync_engine.py` | 338 | Bidirectional sync, state tracking, history |
| `server/tui.py` | 241 | Curses TUI, 4 panels, threaded sync |
| `server/gdrive.py` | 219 | Google Drive API, OAuth2, resumable upload |
| `server/chunk_hash.py` | 212 | SHA256 chunk delta, cache, extract/apply |
| `server/conflict.py` | 209 | 19 conflict scenarios, backup, resolution |
| `server/git_sync.py` | 190 | GitPython: commit, push, pull, log, diff |
| `server/watcher.py` | 173 | watchdog events, debounce, .syncignore |
| `server/ssh_sync.py` | 162 | paramiko SFTP, atomic writes |
| `server/credentials.py` | 157 | Fernet encryption, PBKDF2, master password |
| `server/config.py` | 149 | YAML config, defaults, env overrides |
| `client/` | 3558 | Workshop-DIY dashboard, websocket client |
| `tests.py` | 466 | 114 deep tests |

## Websocket Protocol

Client → Server: `sync:start` `sync:stop` `sync:manual` `status:get` `files:tree` `conflicts:list` `conflicts:resolve` `conflict:diff` `sync:history` `config:get` `config:set` `auth:login` `git:status` `git:commit` `git:push` `git:pull` `git:log` `git:diff` `gdrive:status` `gdrive:list` `dest:list` `dest:test`

Server → Client: `status` `file:changed` `sync:progress` `sync:complete` `conflict:new` `conflict:diff` `log:entry` `config` `auth:required` `auth:ok` `git:status` `git:log` `dest:list` `creds:status`

## Security

Localhost-only, bcrypt auth, Fernet-encrypted credentials (PBKDF2 100k iterations), .credentials/ at 700/600 permissions, no secrets in logs or websocket.
