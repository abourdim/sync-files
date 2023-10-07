#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# SyncFiles — Launcher v1.6
# Compact menu + submenus. All features accessible via --flags too.
# Usage: ./sync.sh [--option] [args]
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

APP_NAME="sync-files"
APP_VERSION="1.6"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
STATE_DIR="$SCRIPT_DIR/.sync_state"
CREDS_DIR="$SCRIPT_DIR/.credentials"
PID_FILE="$STATE_DIR/watcher.pid"
SERVER_PID="$STATE_DIR/server.pid"
CONFIG_FILE="$SCRIPT_DIR/config.yaml"
LOG_FILE="$SCRIPT_DIR/sync.log"
BACKUP_DIR="$SCRIPT_DIR/.backups"
DEFAULT_PORT=8765

# ── Colors ──
R='\033[0;31m'  G='\033[0;32m'  Y='\033[1;33m'  B='\033[0;34m'
C='\033[0;36m'  M='\033[0;35m'  W='\033[1;37m'  D='\033[0;90m'
BG_B='\033[44m' BOLD='\033[1m'  N='\033[0m'

ok()   { echo -e "  ${G}✅ $1${N}"; }
fail() { echo -e "  ${R}❌ $1${N}"; }
warn() { echo -e "  ${Y}⚠️  $1${N}"; }
info() { echo -e "  ${C}ℹ️  $1${N}"; }
step() { echo -e "  ${M}➜${N} $1"; }
hr()   { echo -e "  ${D}$(printf '─%.0s' {1..40})${N}"; }
blank(){ echo ""; }

banner() {
  echo -e "  ${M}╔══════════════════════════════════════╗${N}"
  echo -e "  ${M}║${N}  ${W}${BOLD}$1${N}$(printf '%*s' $((35 - ${#1})) '')${M}║${N}"
  echo -e "  ${M}╚══════════════════════════════════════╝${N}"
}

pause() { blank; echo -n "  Press Enter..."; read -r; }

# ── Platform ──
detect_os() {
  case "$(uname -s)" in
    Linux*) OS="linux";; Darwin*) OS="mac";;
    MINGW*|MSYS*|CYGWIN*) OS="windows";; *) OS="unknown";;
  esac
  grep -qEi "(Microsoft|WSL)" /proc/version 2>/dev/null && OS="wsl" || true
}

get_python() {
  for cmd in python3 python; do
    command -v "$cmd" &>/dev/null && echo "$cmd" && return
  done
}

get_port() {
  [ -f "$CONFIG_FILE" ] && grep -E "^\s+port:" "$CONFIG_FILE" 2>/dev/null | head -1 | awk '{print $2}' | grep -E '^[0-9]+$' || echo "$DEFAULT_PORT"
}

is_port_in_use() {
  local p=$1
  if command -v ss &>/dev/null; then ss -tlnp 2>/dev/null | grep -q ":${p} "
  elif command -v lsof &>/dev/null; then lsof -i ":${p}" &>/dev/null
  elif command -v netstat &>/dev/null; then netstat -tlnp 2>/dev/null | grep -q ":${p} "
  else return 1; fi
}

kill_port() { local p=$1
  command -v fuser &>/dev/null && fuser -k "${p}/tcp" 2>/dev/null || \
  command -v lsof &>/dev/null && lsof -ti ":${p}" | xargs kill -9 2>/dev/null || true
  sleep 1
}

activate_venv() {
  if [ -f "$VENV_DIR/bin/activate" ]; then source "$VENV_DIR/bin/activate"
  elif [ -f "$VENV_DIR/Scripts/activate" ]; then source "$VENV_DIR/Scripts/activate"
  else fail "No venv. Run Setup first."; return 1; fi
}

run_py() { activate_venv || return 1; cd "$SCRIPT_DIR"; python3 -c "import sys;sys.path.insert(0,'.');$1"; }

pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

# Portable sed in-place (macOS needs -i '', Linux needs -i)
sed_i() {
  if [ "$OS" = "mac" ]; then sed -i '' "$@"; else sed -i "$@"; fi
}

get_source() { [ -f "$CONFIG_FILE" ] && grep -E "^\s+source:" "$CONFIG_FILE" 2>/dev/null | head -1 | awk '{print $2}' | tr -d "'\""; }

# ═══════════════════════════════════════════════════════════════════
# CORE ACTIONS
# ═══════════════════════════════════════════════════════════════════

do_wizard() {
  blank; banner "🧙 Setup Wizard"; hr
  info "5 steps to get running."
  blank

  # 1. Install
  step "Step 1/5 — Install dependencies"
  local PY=$(get_python)
  [ -z "$PY" ] && { fail "Python not found"; return 1; }
  [ ! -d "$VENV_DIR" ] && { $PY -m venv "$VENV_DIR"; ok "venv created"; } || ok "venv exists"
  activate_venv
  pip install -r "$SCRIPT_DIR/requirements.txt" --quiet --upgrade
  ok "Dependencies ready"
  mkdir -p "$STATE_DIR" "$BACKUP_DIR"
  [ ! -d "$CREDS_DIR" ] && { mkdir -p "$CREDS_DIR"; chmod 700 "$CREDS_DIR"; }

  if [ ! -f "$CONFIG_FILE" ]; then
    cat > "$CONFIG_FILE" << 'YAML'
sync:
  source: ''
  destinations: []
  watch: true
  interval: 5
  chunk_size: 4194304
  debounce: 1.0
server:
  host: 127.0.0.1
  port: 8765
  bind_network: false
  auto_open_browser: true
  session_timeout: 1800
git:
  enabled: false
  auto_commit: false
  auto_push: false
  branch: main
  commit_template: 'sync: {timestamp} — {files_changed} files'
log:
  file: sync.log
  max_size: 10485760
  level: info
YAML
  fi

  [ ! -f "$SCRIPT_DIR/.syncignore" ] && cat > "$SCRIPT_DIR/.syncignore" << 'IGN'
.venv/
__pycache__/
*.pyc
*.pyo
node_modules/
.vscode/
.idea/
*.swp
*~
.DS_Store
Thumbs.db
.credentials/
.sync_state/
sync.log
*.pid
*.sync_tmp
.backups/
IGN
  hr

  # 2. Source
  step "Step 2/5 — Source path"
  echo -n "  Your project folder: "; read -r src_path
  if [ -n "$src_path" ] && [ -d "$src_path" ]; then
    sed_i "s|source:.*|source: '$src_path'|" "$CONFIG_FILE"; ok "Source → $src_path"
  else warn "Skipped — set later in config.yaml"; fi
  hr

  # 3. Destination
  step "Step 3/5 — Destination"
  echo -n "  Backup folder (or Enter to skip): "; read -r dst_path
  if [ -n "$dst_path" ] && [ -d "$dst_path" ]; then
    echo -n "  Name for this destination: "; read -r dst_name
    sed_i 's/destinations: \[\]/destinations:/' "$CONFIG_FILE"
    echo "    - type: local" >> "$CONFIG_FILE"
    echo "      name: ${dst_name:-backup}" >> "$CONFIG_FILE"
    echo "      path: '$dst_path'" >> "$CONFIG_FILE"
    ok "Destination → $dst_path"
  else warn "Skipped"; fi
  hr

  # 4. Git
  step "Step 4/5 — Git"
  if [ -n "$src_path" ] && [ -d "$src_path/.git" ] 2>/dev/null; then
    ok "Git repo detected"
    echo -n "  Enable auto-commit? [y/N] "; read -r gc
    [[ "$gc" =~ ^[Yy]$ ]] && sed_i 's/auto_commit: false/auto_commit: true/' "$CONFIG_FILE"
    sed_i 's/enabled: false/enabled: true/' "$CONFIG_FILE"
  else info "No git repo — skipped"; fi
  hr

  # 5. Credentials
  step "Step 5/5 — Credential store"
  echo -n "  Create encrypted store? [Y/n] "; read -r cr
  if [[ ! "$cr" =~ ^[Nn]$ ]]; then
    run_py "
from server.credentials import CredentialStore,prompt_master_password
cs=CredentialStore('$SCRIPT_DIR')
if cs.is_initialized():print('  Already exists')
else:pwd=prompt_master_password(confirm=True);cs.initialize(pwd);print('  ✅ Created')
"
  fi

  hr; echo -e "  ${G}${BOLD}🧙 All set!${N}"; blank
  echo -e "  Try: ${C}./sync.sh --web${N} or ${C}./sync.sh --sync${N}"
  blank
}

do_web() {
  blank; banner "🌐 Web Dashboard"; hr
  local port=$(get_port)
  if is_port_in_use "$port"; then
    warn "Port $port busy"
    echo -n "  Kill it? [y/N] "; read -r ans
    [[ "$ans" =~ ^[Yy]$ ]] && { kill_port "$port"; ok "Freed"; } || { fail "Port busy"; return 1; }
  fi
  activate_venv || return 1; cd "$SCRIPT_DIR"
  step "Starting on port $port..."
  python3 -m server.app "$CONFIG_FILE" &
  local pid=$!; echo "$pid" > "$SERVER_PID"; sleep 1
  if kill -0 "$pid" 2>/dev/null; then
    ok "Running (PID: $pid)"; blank
    echo -e "  ${G}${BOLD}🔗 http://127.0.0.1:${port}${N}"; blank
    case "$OS" in
      mac) open "http://127.0.0.1:${port}" 2>/dev/null & ;;
      wsl) cmd.exe /c start "http://127.0.0.1:${port}" 2>/dev/null & ;;
      linux) command -v xdg-open &>/dev/null && xdg-open "http://127.0.0.1:${port}" 2>/dev/null & ;;
    esac
    info "Ctrl+C to stop"; wait "$pid" 2>/dev/null || true
  else fail "Failed — check sync.log"; fi
}

do_tui() {
  blank; banner "🖥️  Terminal UI"; hr
  activate_venv || return 1; cd "$SCRIPT_DIR"
  python3 -m server.tui "$CONFIG_FILE"
}

do_sync() {
  blank; banner "🔄 Sync Now"; hr
  run_py "
from server.config import Config;from server.sync_engine import SyncEngine
c=Config('$CONFIG_FILE');e=SyncEngine(c,event_callback=lambda t,d:print(f'  [{t}] {d.get(\"msg\",d)}')if isinstance(d,dict)and d.get('msg')else None)
dests=c.get('sync','destinations')or[]
if not dests:print('  ❌ No destinations in config.yaml');exit()
for d in dests:
    n=d.get('name',d.get('path','?'));print(f'  → {n}')
    s=e.sync(d);print(f'  📊 ↑{s[\"uploaded\"]} ↓{s[\"downloaded\"]} ⚠{s[\"conflicts\"]} ✗{s[\"errors\"]} ({s.get(\"duration\",0)}s)')
"
  blank
}

do_status() {
  blank; banner "📊 Status"; hr
  run_py "
from server.config import Config;from server.sync_engine import SyncEngine;from server.git_sync import GitSync
c=Config('$CONFIG_FILE');e=SyncEngine(c);s=e.get_status()
print(f'  Last sync:  {s[\"last_sync\"]or\"never\"}')
print(f'  Files:      {s[\"files_tracked\"]}  Pending: {s[\"files_pending\"]}  Conflicts: {s[\"conflicts\"]}')
src=c.get('sync','source')or'.'
g=GitSync(src,c)
if g.is_repo():
    try:gs=g.status();print(f'  Git:        {gs[\"branch\"]} {\"●\"if gs[\"is_dirty\"]else\"✓\"}')
    except:pass
dests=c.get('sync','destinations')or[]
print(f'  Destinations: {len(dests)}')
"
  pid_alive "$PID_FILE" && ok "Auto-sync: running" || info "Auto-sync: stopped"
  pid_alive "$SERVER_PID" && ok "Web server: running" || info "Web server: stopped"
  blank
}

# ═══════════════════════════════════════════════════════════════════
# SETTINGS SUBMENU
# ═══════════════════════════════════════════════════════════════════

do_settings_menu() {
  while true; do
    blank; banner "⚙️  Settings"; hr
    echo -e "  ${C}1)${N} Edit config.yaml"
    echo -e "  ${C}2)${N} Edit .syncignore"
    echo -e "  ${C}3)${N} View config"
    echo -e "  ${C}4)${N} Credentials ►"
    echo -e "  ${C}5)${N} Start auto-sync"
    echo -e "  ${C}6)${N} Stop auto-sync"
    echo -e "  ${C}7)${N} Check ports"
    echo -e "  ${C}8)${N} View log"
    echo -e "  ${C}9)${N} Follow log (live)"
    echo -e "  ${C}0)${N} Back"; hr
    echo -n "  Choice: "; read -r ch
    case $ch in
      1) ${EDITOR:-nano} "$CONFIG_FILE" ;;
      2) ${EDITOR:-nano} "$SCRIPT_DIR/.syncignore" ;;
      3) blank; [ -f "$CONFIG_FILE" ] && cat "$CONFIG_FILE" || warn "No config"; pause ;;
      4) do_creds_menu ;;
      5) do_start ;;
      6) do_stop ;;
      7) do_ports ;;
      8) do_log 50 ;;
      9) do_log "-f" ;;
      0|q) break ;;
    esac
  done
}

do_start() {
  blank
  if pid_alive "$PID_FILE"; then warn "Already running (PID: $(cat "$PID_FILE"))"; return; fi
  activate_venv || return 1; cd "$SCRIPT_DIR"
  python3 -c "
import sys,time,signal;sys.path.insert(0,'.')
from server.config import Config;from server.sync_engine import SyncEngine;from server.watcher import FileWatcher,SyncIgnore
c=Config('$CONFIG_FILE');e=SyncEngine(c);src=c.get('sync','source')or'.'
w=FileWatcher(src,e.handle_file_events,sync_ignore=SyncIgnore(src),debounce=c.get('sync','debounce')or 1.0)
w.start();print(f'Watching: {src}')
signal.signal(signal.SIGINT,lambda s,f:(w.stop(),sys.exit(0)));signal.signal(signal.SIGTERM,lambda s,f:(w.stop(),sys.exit(0)))
while True:
    time.sleep(c.get('sync','interval')or 5)
    for d in(c.get('sync','destinations')or[]):e.sync(d)
" &
  echo "$!" > "$PID_FILE"; ok "Auto-sync started (PID: $!)"
}

do_stop() {
  blank
  if ! pid_alive "$PID_FILE"; then warn "Not running"; rm -f "$PID_FILE"; return; fi
  local pid=$(cat "$PID_FILE"); kill "$pid" 2>/dev/null; sleep 1
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
  rm -f "$PID_FILE"; ok "Stopped"
}

do_ports() {
  blank; local port=$(get_port)
  if is_port_in_use "$port"; then
    fail "Port $port IN USE"
    echo -n "  Kill? [y/N] "; read -r ans
    [[ "$ans" =~ ^[Yy]$ ]] && { kill_port "$port"; ok "Freed"; }
  else ok "Port $port available"; fi
  pid_alive "$SERVER_PID" && ok "Web: PID $(cat "$SERVER_PID")" || true
  pid_alive "$PID_FILE" && ok "Sync: PID $(cat "$PID_FILE")" || true
}

do_log() {
  local arg="${1:-50}"
  [ ! -f "$LOG_FILE" ] && { warn "No log yet"; return; }
  if [ "$arg" = "-f" ] || [ "$arg" = "follow" ]; then
    info "Ctrl+C to stop"; tail -f "$LOG_FILE"
  else tail -n "$arg" "$LOG_FILE"; fi
}

do_creds_menu() {
  while true; do
    blank; banner "🔑 Credentials"; hr
    echo -e "  ${C}1)${N} Init / unlock store"
    echo -e "  ${C}2)${N} Dashboard password"
    echo -e "  ${C}3)${N} Google Drive status"
    echo -e "  ${C}4)${N} Git / SSH key status"
    echo -e "  ${C}5)${N} Change master password"
    echo -e "  ${C}6)${N} Health check (test all)"
    echo -e "  ${C}0)${N} Back"; hr
    echo -n "  Choice: "; read -r ch
    case $ch in
      1) run_py "
from server.credentials import CredentialStore,prompt_master_password
cs=CredentialStore('$SCRIPT_DIR')
if cs.is_initialized():
    print('  Store exists — unlocking...');pwd=prompt_master_password()
    if cs.unlock(pwd):print('  ✅ Unlocked');[print(f'    {s}: {k}')for s,k in cs.list_services().items()]
    else:print('  ❌ Wrong password')
else:print('  Creating...');pwd=prompt_master_password(confirm=True);cs.initialize(pwd);print('  ✅ Created')
" ;;
      2) run_py "
import bcrypt,getpass;from server.credentials import CredentialStore,prompt_master_password
cs=CredentialStore('$SCRIPT_DIR')
if not cs.is_initialized():print('  Init store first (option 1)');exit()
pwd=prompt_master_password()
if not cs.unlock(pwd):print('  ❌ Wrong password');exit()
np=getpass.getpass('  New dashboard password: ');c2=getpass.getpass('  Confirm: ')
if np!=c2:print('  ❌ Mismatch');exit()
cs.set('dashboard','password_hash',bcrypt.hashpw(np.encode(),bcrypt.gensalt()).decode());print('  ✅ Set')
" ;;
      3) run_py "
from server.gdrive import GDriveSync;from server.credentials import CredentialStore,prompt_master_password
cs=CredentialStore('$SCRIPT_DIR')
if not cs.is_initialized():print('  Init store first');exit()
pwd=prompt_master_password()
if not cs.unlock(pwd):print('  ❌ Wrong password');exit()
gd=GDriveSync(cs);r=gd.test_connection()
print(f'  ✅ {r[\"user\"]}'if r.get('ok')else f'  ❌ {r.get(\"error\",\"Not configured\")}')
" ;;
      4) blank
         [ -f "$HOME/.ssh/id_ed25519" ] && ok "SSH: ~/.ssh/id_ed25519" || \
         { [ -f "$HOME/.ssh/id_rsa" ] && ok "SSH: ~/.ssh/id_rsa" || warn "No SSH key"; }
         command -v git &>/dev/null && {
           local rem=$(git -C "$SCRIPT_DIR" remote get-url origin 2>/dev/null)
           [ -n "$rem" ] && ok "Git: $rem" || warn "No git remote"
         } ;;
      5) run_py "
import getpass;from server.credentials import CredentialStore
cs=CredentialStore('$SCRIPT_DIR')
if not cs.is_initialized():print('  ❌ Not initialized');exit()
old=getpass.getpass('  Current: ');new=getpass.getpass('  New: ');c2=getpass.getpass('  Confirm: ')
if new!=c2:print('  ❌ Mismatch')
elif cs.change_master(old,new):print('  ✅ Changed')
else:print('  ❌ Wrong password')
" ;;
      6) run_py "
from server.credentials import CredentialStore,prompt_master_password;from server.git_sync import GitSync;from server.gdrive import GDriveSync
cs=CredentialStore('$SCRIPT_DIR')
if not cs.is_initialized():print('  ❌ Store not initialized');exit()
pwd=prompt_master_password()
if not cs.unlock(pwd):print('  ❌ Wrong password');exit()
g=GitSync('.');r=g.test_connection()if g.is_repo()else{'ok':False,'error':'Not a repo'}
print(f'  Git:   {\"✅ \"+r.get(\"url\",\"\")if r[\"ok\"]else\"⚠️  \"+r.get(\"error\",\"\")}')
gd=GDriveSync(cs)
if gd.is_configured():r=gd.test_connection();print(f'  Drive: {\"✅ \"+r.get(\"user\",\"\")if r[\"ok\"]else\"❌ \"+r.get(\"error\",\"\")}')
else:print('  Drive: ⚠️  Not configured')
" ;;
      0|q) break ;;
    esac
  done
}

# ═══════════════════════════════════════════════════════════════════
# TOOLS SUBMENU
# ═══════════════════════════════════════════════════════════════════

do_tools_menu() {
  while true; do
    blank; banner "🔧 Tools"; hr
    echo -e "  ${C}1)${N} 🩺 Doctor (full health check)"
    echo -e "  ${C}2)${N} 🧪 Run tests"
    echo -e "  ${C}3)${N} ⏱  Benchmark sync"
    echo -e "  ${C}4)${N} 👁  Watch mode (live terminal)"
    echo -e "  ${C}5)${N} 💾 Backup"
    echo -e "  ${C}6)${N} ♻️  Restore"
    echo -e "  ${C}7)${N} 📤 Export config (new machine)"
    echo -e "  ${C}8)${N} 🔄 Update (git pull + deps)"
    echo -e "  ${C}9)${N} 🧹 Clean"
    echo -e "  ${C}0)${N} Back"; hr
    echo -n "  Choice: "; read -r ch
    case $ch in
      1) do_doctor ;;
      2) do_tests ;;
      3) do_benchmark ;;
      4) do_watch ;;
      5) do_backup ;;
      6) do_restore ;;
      7) do_export ;;
      8) do_update ;;
      9) do_clean ;;
      0|q) break ;;
    esac
  done
}

do_doctor() {
  blank; banner "🩺 Doctor"; hr
  local issues=0

  step "Python..."; local PY=$(get_python)
  [ -n "$PY" ] && ok "$($PY --version 2>&1)" || { fail "Not found"; issues=$((issues+1)); }

  step "Venv..."; [ -d "$VENV_DIR" ] && ok "exists" || { fail "Missing"; issues=$((issues+1)); }

  step "Deps..."
  if activate_venv 2>/dev/null; then
    local miss=0
    while IFS= read -r pkg; do
      local pn=$(echo "$pkg"|cut -d'>' -f1|cut -d'=' -f1|tr -d ' '); [ -z "$pn" ] && continue
      pip show "$pn" &>/dev/null || { warn "Missing: $pn"; miss=$((miss+1)); }
    done < "$SCRIPT_DIR/requirements.txt"
    deactivate 2>/dev/null||true; [ $miss -eq 0 ] && ok "All installed" || issues=$((issues+miss))
  fi

  step "Config..."
  [ -f "$CONFIG_FILE" ] && ok "config.yaml" || { fail "Missing"; issues=$((issues+1)); }
  local src=$(get_source)
  [ -n "$src" ] && [ -d "$src" ] && ok "Source: $src" || { [ -n "$src" ] && fail "Source missing: $src" && issues=$((issues+1)) || warn "Source not set"; }

  step "Credentials..."
  [ -d "$CREDS_DIR" ] && [ -f "$CREDS_DIR/store.enc" ] && ok "Store initialized" || warn "Not initialized"
  [ -d "$CREDS_DIR" ] && { local perm=$(stat -c '%a' "$CREDS_DIR" 2>/dev/null||stat -f '%Lp' "$CREDS_DIR" 2>/dev/null)
    [ "$perm" = "700" ] && ok "Permissions: 700" || warn "Permissions: $perm"; }

  step "Git..."; command -v git &>/dev/null && [ -d "$SCRIPT_DIR/.git" ] && ok "$(git -C "$SCRIPT_DIR" branch --show-current 2>/dev/null||echo repo)" || info "No repo"

  step "Ports..."; local port=$(get_port)
  is_port_in_use "$port" && { pid_alive "$SERVER_PID" && ok "Port $port: SyncFiles" || warn "Port $port: other process"; } || ok "Port $port: free"

  step "Disk..."; ok "Free: $(df -h "$SCRIPT_DIR"|tail -1|awk '{print $4}')"

  step "Daemons..."
  pid_alive "$SERVER_PID" && ok "Web: PID $(cat "$SERVER_PID")" || info "Web: stopped"
  pid_alive "$PID_FILE" && ok "Sync: PID $(cat "$PID_FILE")" || info "Sync: stopped"

  step "Tests..."
  [ -f "$SCRIPT_DIR/tests.py" ] && ok "tests.py present" || warn "Missing"

  hr
  [ $issues -eq 0 ] && echo -e "  ${G}${BOLD}🩺 All healthy!${N}" || echo -e "  ${Y}${BOLD}🩺 $issues issue(s)${N}"
  blank
}

do_tests() {
  blank; banner "🧪 Tests"; hr
  [ ! -f "$SCRIPT_DIR/tests.py" ] && { fail "tests.py not found"; return; }
  activate_venv || return 1; cd "$SCRIPT_DIR"
  local t1=$(date +%s)
  python3 tests.py 2>&1 | tail -5
  local t2=$(date +%s); hr; info "Done in $((t2-t1))s"; blank
}

do_benchmark() {
  blank; banner "⏱  Benchmark"; hr
  run_py "
import time;from server.config import Config;from server.sync_engine import SyncEngine
c=Config('$CONFIG_FILE');e=SyncEngine(c);dests=c.get('sync','destinations')or[]
if not dests:print('  ❌ No destinations');exit()
for d in dests:
    n=d.get('name',d.get('path','?'));print(f'  {n}:')
    t=time.time();s=e.sync(d);dt=round(time.time()-t,3)
    total=s['uploaded']+s['downloaded']+s.get('skipped',0)
    fps=round(total/dt,1)if dt>0 else 0
    print(f'    {dt}s — ↑{s[\"uploaded\"]} ↓{s[\"downloaded\"]} skip={s.get(\"skipped\",0)} — {fps} files/s')
"
  blank
}

do_watch() {
  blank; info "Live mode — Ctrl+C to stop"; blank
  while true; do
    clear 2>/dev/null||true
    echo -e "  ${W}${BOLD}🔄 SyncFiles${N}  $(date '+%H:%M:%S')"
    hr
    run_py "
from server.config import Config;from server.sync_engine import SyncEngine
c=Config('$CONFIG_FILE');e=SyncEngine(c);s=e.get_status()
print(f'  Files: {s[\"files_tracked\"]}  Pending: {s[\"files_pending\"]}  Conflicts: {s[\"conflicts\"]}  Last: {(s[\"last_sync\"]or\"never\")[:19]}')
" 2>/dev/null || echo "  (unavailable)"
    pid_alive "$SERVER_PID" && echo -e "  ${G}● Web${N}" || echo -e "  ${D}○ Web${N}"
    pid_alive "$PID_FILE" && echo -e "  ${G}● Sync${N}" || echo -e "  ${D}○ Sync${N}"
    hr; echo -e "  ${D}Log:${N}"
    [ -f "$LOG_FILE" ] && tail -6 "$LOG_FILE"|while IFS= read -r l;do echo "  $l";done || echo "  (empty)"
    sleep 3
  done
}

do_backup() {
  blank; mkdir -p "$BACKUP_DIR"
  local name="backup_$(date +%Y%m%d_%H%M%S).tar.gz"
  tar czf "$BACKUP_DIR/$name" -C "$SCRIPT_DIR" config.yaml .syncignore .sync_state .credentials 2>/dev/null || true
  [ -f "$BACKUP_DIR/$name" ] && ok "$name ($(du -h "$BACKUP_DIR/$name"|awk '{print $1}'))" || fail "Failed"
  info "$(ls "$BACKUP_DIR"/backup_*.tar.gz 2>/dev/null|wc -l) backup(s) total"
}

do_restore() {
  blank; local bk=($(ls -t "$BACKUP_DIR"/backup_*.tar.gz 2>/dev/null))
  [ ${#bk[@]} -eq 0 ] && { warn "No backups"; return; }
  echo -e "  ${W}Backups:${N}"
  for i in "${!bk[@]}"; do echo -e "  ${C}$((i+1)))${N} $(basename "${bk[$i]}") ($(du -h "${bk[$i]}"|awk '{print $1}'))"; done
  echo -n "  Choose (0=cancel): "; read -r ch; [ "$ch" = "0" ] || [ -z "$ch" ] && return
  local idx=$((ch-1)); [ $idx -lt 0 ] || [ $idx -ge ${#bk[@]} ] && { fail "Invalid"; return; }
  echo -n "  Restore? [y/N] "; read -r ans
  [[ "$ans" =~ ^[Yy]$ ]] && { tar xzf "${bk[$idx]}" -C "$SCRIPT_DIR"; ok "Restored"; }
}

do_export() {
  blank; local out="$SCRIPT_DIR/sync-config-$(date +%Y%m%d).tar.gz"
  tar czf "$out" -C "$SCRIPT_DIR" config.yaml .syncignore requirements.txt 2>/dev/null
  [ -f "$out" ] && ok "$(basename "$out") — copy to new machine, extract, run --install" || fail "Failed"
}

do_update() {
  blank; banner "🔄 Update"; hr
  [ -d "$SCRIPT_DIR/.git" ] && { step "Pulling..."; cd "$SCRIPT_DIR"; git pull 2>&1|while IFS= read -r l;do echo "    $l";done; ok "Code updated"; } || info "Not a git repo"
  step "Deps..."; activate_venv||return 1; pip install -r "$SCRIPT_DIR/requirements.txt" --quiet --upgrade; ok "Updated"
  deactivate 2>/dev/null||true; info "Version: $APP_VERSION"; blank
}

do_clean() {
  blank; banner "🧹 Clean"; hr
  echo -e "  ${C}1)${N} State (.sync_state/)"
  echo -e "  ${C}2)${N} Credentials (.credentials/)"
  echo -e "  ${C}3)${N} Venv (.venv/)"
  echo -e "  ${C}4)${N} Log"
  echo -e "  ${C}5)${N} Backups"
  echo -e "  ${R}6)${N} ${BOLD}EVERYTHING${N}"
  echo -e "  ${C}0)${N} Cancel"; hr
  echo -n "  Choice: "; read -r ch
  case $ch in
    1) rm -rf "$STATE_DIR"; ok "State removed" ;;
    2) echo -n "  Sure? [y/N] "; read -r a; [[ "$a" =~ ^[Yy]$ ]] && { rm -rf "$CREDS_DIR"; ok "Removed"; } ;;
    3) rm -rf "$VENV_DIR"; ok "Venv removed" ;;
    4) rm -f "$LOG_FILE"; ok "Log removed" ;;
    5) rm -rf "$BACKUP_DIR"; ok "Backups removed" ;;
    6) echo -n "  Type 'yes': "; read -r a
       [ "$a" = "yes" ] && { rm -rf "$VENV_DIR" "$STATE_DIR" "$CREDS_DIR" "$BACKUP_DIR" "$LOG_FILE" "$CONFIG_FILE"; ok "Clean. Run --install."; } ;;
    0|q) ;;
  esac
}

do_help() {
  blank; banner "📖 Help"; hr
  echo -e "  ${C}v$APP_VERSION${N} | ${C}$OS${N}"
  blank
  echo -e "  ${W}Essentials:${N}"
  echo "    --wizard      Interactive setup"
  echo "    --web         Web dashboard"
  echo "    --tui         Terminal UI"
  echo "    --sync        Manual sync"
  echo "    --status      Quick status"
  blank
  echo -e "  ${W}Settings:${N}"
  echo "    --config      Config menu"
  echo "    --creds       Credentials menu"
  echo "    --start       Auto-sync daemon"
  echo "    --stop        Stop daemon"
  echo "    --ports       Port check"
  echo "    --log [N|-f]  View/follow log"
  blank
  echo -e "  ${W}Tools:${N}"
  echo "    --doctor      Health check"
  echo "    --tests       Run tests"
  echo "    --benchmark   Time a sync"
  echo "    --watch       Live terminal"
  echo "    --backup      Backup state"
  echo "    --restore     Restore backup"
  echo "    --export      Portable config"
  echo "    --update      Git pull + deps"
  echo "    --clean       Wipe data"
  blank
  echo "    --version     Version"
  echo "    --help        This help"
  blank
}

# ═══════════════════════════════════════════════════════════════════
# MAIN MENU — 9 items, clean
# ═══════════════════════════════════════════════════════════════════

show_menu() {
  clear 2>/dev/null||true; blank
  echo -e "  ${M}╔══════════════════════════════════════╗${N}"
  echo -e "  ${M}║${N}       ${W}${BOLD}🔄 SyncFiles${N} ${D}v${APP_VERSION}${N}             ${M}║${N}"
  echo -e "  ${M}╠══════════════════════════════════════╣${N}"
  echo -e "  ${M}║${N}                                      ${M}║${N}"
  echo -e "  ${M}║${N}  ${C}1)${N} 🧙 Setup wizard                 ${M}║${N}"
  echo -e "  ${M}║${N}  ${G}2)${N} 🌐 Web Dashboard                ${M}║${N}"
  echo -e "  ${M}║${N}  ${G}3)${N} 🖥️  Terminal UI                  ${M}║${N}"
  echo -e "  ${M}║${N}  ${B}4)${N} 🔄 Sync now                     ${M}║${N}"
  echo -e "  ${M}║${N}  ${Y}5)${N} 📊 Status                       ${M}║${N}"
  echo -e "  ${M}║${N}  ${W}6)${N} ⚙️  Settings ►                   ${M}║${N}"
  echo -e "  ${M}║${N}  ${W}7)${N} 🔧 Tools ►                      ${M}║${N}"
  echo -e "  ${M}║${N}  ${D}8)${N} 📖 Help                         ${M}║${N}"
  echo -e "  ${M}║${N}  ${R}0)${N} Exit                            ${M}║${N}"
  echo -e "  ${M}║${N}                                      ${M}║${N}"
  echo -e "  ${M}╚══════════════════════════════════════╝${N}"
  blank; echo -n "  Choice: "
}

menu_loop() {
  while true; do
    show_menu; read -r choice
    case $choice in
      1) do_wizard; pause ;;
      2) do_web ;;
      3) do_tui ;;
      4) do_sync; pause ;;
      5) do_status; pause ;;
      6) do_settings_menu ;;
      7) do_tools_menu ;;
      8) do_help; pause ;;
      0|q|Q|exit) blank; echo -e "  ${D}👋 Bye!${N}"; blank; exit 0 ;;
      *) warn "Invalid" ;;
    esac
  done
}

# ═══════════════════════════════════════════════════════════════════
# CLI DISPATCH — all 23 flags still work
# ═══════════════════════════════════════════════════════════════════

detect_os
trap 'echo "";echo -e "  ${D}👋${N}";exit 0' INT

case "${1:-}" in
  --check)     do_doctor ;;
  --install)   do_wizard ;;
  --wizard)    do_wizard ;;
  --web)       do_web ;;
  --tui)       do_tui ;;
  --watch)     do_watch ;;
  --sync)      do_sync ;;
  --start)     do_start ;;
  --stop)      do_stop ;;
  --status)    do_status ;;
  --log)       do_log "${2:-50}" ;;
  --ports)     do_ports ;;
  --benchmark) do_benchmark ;;
  --config)    do_settings_menu ;;
  --creds)     do_creds_menu ;;
  --backup)    do_backup ;;
  --restore)   do_restore ;;
  --export)    do_export ;;
  --doctor)    do_doctor ;;
  --tests)     do_tests ;;
  --update)    do_update ;;
  --clean)     do_clean ;;
  --version)   echo "SyncFiles v$APP_VERSION" ;;
  --help|-h)   do_help ;;
  "")          menu_loop ;;
  *)           fail "Unknown: $1"; do_help ;;
esac
