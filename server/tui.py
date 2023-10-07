"""
SyncFiles — Terminal UI (TUI)
Curses-based terminal interface for sync monitoring and control.
"""

import sys
import os
import time
import curses
import json
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from server.config import Config
from server.sync_engine import SyncEngine
from server.watcher import FileWatcher, SyncIgnore
from server.git_sync import GitSync


class TUI:
    def __init__(self, config_path='config.yaml'):
        self.config = Config(config_path)
        self.source = self.config.get('sync', 'source') or '.'
        self.engine = SyncEngine(self.config, event_callback=self._on_event)
        self.git = GitSync(self.source, self.config)
        self.watcher = None
        self.log_lines = []
        self.running = True
        self.panel = 'status'
        self.scroll = 0
        self.auto_sync = False
        self._lock = threading.Lock()

    def _on_event(self, etype, data):
        msg = data.get('msg', '') if isinstance(data, dict) else str(data)
        if msg:
            with self._lock:
                self.log_lines.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
                self.log_lines = self.log_lines[-200:]

    def _log(self, msg):
        with self._lock:
            self.log_lines.append(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def start(self):
        if self.source and Path(self.source).exists():
            ignore = SyncIgnore(self.source)
            self.watcher = FileWatcher(
                self.source, self.engine.handle_file_events,
                sync_ignore=ignore, debounce=self.config.get('sync', 'debounce') or 1.0,
            )
            self.watcher.start()
            self._log(f"Watching: {self.source}")
        try:
            curses.wrapper(self._main)
        except KeyboardInterrupt:
            pass
        finally:
            if self.watcher:
                self.watcher.stop()

    def _main(self, scr):
        curses.curs_set(0)
        scr.nodelay(True)
        scr.timeout(1000)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_YELLOW, -1)
        curses.init_pair(3, curses.COLOR_RED, -1)
        curses.init_pair(4, curses.COLOR_CYAN, -1)
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)
        curses.init_pair(6, curses.COLOR_WHITE, curses.COLOR_BLUE)

        auto_timer = 0
        interval = self.config.get('sync', 'interval') or 5

        while self.running:
            try:
                scr.clear()
                h, w = scr.getmaxyx()
                self._header(scr, w)
                ch = h - 5
                if self.panel == 'status': self._p_status(scr, 3, ch, w)
                elif self.panel == 'files': self._p_files(scr, 3, ch, w)
                elif self.panel == 'conflicts': self._p_conflicts(scr, 3, ch, w)
                elif self.panel == 'log': self._p_log(scr, 3, ch, w)
                self._footer(scr, h, w)
                scr.refresh()

                if self.auto_sync:
                    auto_timer += 1
                    if auto_timer >= interval:
                        auto_timer = 0
                        threading.Thread(target=self._sync, daemon=True).start()

                k = scr.getch()
                if k in (ord('q'), ord('Q')): self.running = False
                elif k in (ord('s'), ord('S')): threading.Thread(target=self._sync, daemon=True).start()
                elif k in (ord('p'), ord('P')):
                    self.auto_sync = not self.auto_sync
                    self._log(f"Auto-sync {'ON' if self.auto_sync else 'OFF'}")
                elif k == ord('1'): self.panel = 'status'; self.scroll = 0
                elif k == ord('2'): self.panel = 'files'; self.scroll = 0
                elif k == ord('3'): self.panel = 'conflicts'; self.scroll = 0
                elif k == ord('4'): self.panel = 'log'; self.scroll = 0
                elif k == curses.KEY_UP: self.scroll = max(0, self.scroll - 1)
                elif k == curses.KEY_DOWN: self.scroll += 1
            except curses.error:
                pass

    def _header(self, s, w):
        try:
            s.attron(curses.color_pair(6) | curses.A_BOLD)
            s.addstr(0, 0, " 🔄 SyncFiles TUI ".center(w))
            s.attroff(curses.color_pair(6) | curses.A_BOLD)
        except curses.error: pass
        tabs = [('1:Status', self.panel=='status'), ('2:Files', self.panel=='files'),
                ('3:Conflicts', self.panel=='conflicts'), ('4:Log', self.panel=='log')]
        x = 1
        for label, active in tabs:
            try:
                if active: s.attron(curses.A_REVERSE)
                s.addstr(1, x, f" {label} ")
                if active: s.attroff(curses.A_REVERSE)
                x += len(label) + 3
            except curses.error: pass
        try: s.addstr(2, 0, "─" * min(w, 200))
        except curses.error: pass

    def _footer(self, s, h, w):
        al = "ON" if self.auto_sync else "OFF"
        bar = f" s=Sync  p=Auto[{al}]  1-4=Panels  ↑↓=Scroll  q=Quit "
        try:
            s.addstr(h-2, 0, "─" * min(w, 200))
            s.attron(curses.A_DIM); s.addstr(h-1, 0, bar[:w-1]); s.attroff(curses.A_DIM)
        except curses.error: pass

    def _p_status(self, s, y, h, w):
        st = self.engine.get_status()
        lines = [
            ("Last Sync", st.get('last_sync') or 'never'),
            ("Files", str(st.get('files_tracked', 0))),
            ("Pending", str(st.get('files_pending', 0))),
            ("Conflicts", str(st.get('conflicts', 0))),
            ("Syncing", "yes" if st.get('syncing') else "no"),
            ("Auto-Sync", "ON" if self.auto_sync else "OFF"),
            ("Watcher", "running" if self.watcher and self.watcher.is_running() else "stopped"),
            ("", ""),
        ]
        if self.git.is_repo():
            try:
                gs = self.git.status()
                lines += [("Git Branch", gs.get('branch','?')), ("Git Dirty", "yes" if gs.get('is_dirty') else "no"),
                          ("Git Remote", "yes" if gs.get('has_remote') else "no")]
            except: lines.append(("Git", "error"))
        else: lines.append(("Git", "not a repo"))
        lines += [("", ""), ("Source", self.source)]
        for i, d in enumerate(self.config.get('sync', 'destinations') or []):
            lines.append((f"Dest {i+1}", f"{d.get('type','?')}: {d.get('name', d.get('path','?'))}"))
        for i, (l, v) in enumerate(lines):
            if i >= h: break
            try:
                if l:
                    s.addstr(y+i, 2, f"{l}:", curses.A_BOLD)
                    c = curses.color_pair(1) if v in ('yes','ON','running') else curses.color_pair(3) if v in ('no','OFF','stopped','never','error') else 0
                    s.addstr(y+i, 20, v[:w-22], c)
            except curses.error: pass

    def _p_files(self, s, y, h, w):
        tree = self.engine.get_file_tree()
        if not tree:
            try: s.addstr(y+1, 2, "No files tracked", curses.A_DIM)
            except: pass; return
        items = sorted(tree.items())[self.scroll:self.scroll+h]
        icons = {'synced':'✓','pending':'⏳','conflict':'⚠','error':'✗','deleted':'D'}
        colors = {'synced':1,'pending':2,'conflict':3,'error':3,'deleted':5}
        for i, (p, info) in enumerate(items):
            if i >= h: break
            st = info.get('status','?')
            try:
                s.addstr(y+i, 2, icons.get(st,'?'), curses.color_pair(colors.get(st,0)))
                s.addstr(y+i, 5, p[:w-20])
                sz = info.get('size',0)
                s.addstr(y+i, max(w-14,30), self._sz(sz), curses.A_DIM)
            except curses.error: pass

    def _p_conflicts(self, s, y, h, w):
        cl = self.engine.conflicts.list_active()
        if not cl:
            try: s.addstr(y+1, 2, "No conflicts ✓", curses.color_pair(1))
            except: pass; return
        r = y
        for c in cl:
            if r >= y+h-1: break
            try:
                s.addstr(r, 2, "⚠ "+c['path'][:w-4], curses.color_pair(3)|curses.A_BOLD); r += 1
                s.addstr(r, 4, f"{c.get('destination','')} — {c.get('timestamp','')[:19]}"[:w-6], curses.A_DIM); r += 1
            except curses.error: r += 1

    def _p_log(self, s, y, h, w):
        with self._lock: lines = list(self.log_lines)
        if not lines:
            try: s.addstr(y+1, 2, "Log empty", curses.A_DIM)
            except: pass; return
        start = max(0, len(lines) - h)
        for i, line in enumerate(lines[start:start+h]):
            if i >= h: break
            c = 0
            if '❌' in line or 'error' in line.lower(): c = 3
            elif '✅' in line or 'success' in line.lower(): c = 1
            elif 'TX:' in line or '↑' in line: c = 4
            elif 'RX:' in line or '↓' in line: c = 4
            elif '⚠' in line: c = 2
            try: s.addstr(y+i, 1, line[:w-2], curses.color_pair(c))
            except curses.error: pass

    def _sync(self):
        self._log("🔄 Syncing...")
        dests = self.config.get('sync', 'destinations') or []
        if not dests: self._log("❌ No destinations"); return
        for d in dests:
            stats = self.engine.sync(d)
            self._log(f"✅ ↑{stats.get('uploaded',0)} ↓{stats.get('downloaded',0)} ⚠{stats.get('conflicts',0)} ({stats.get('duration',0)}s)")
        if self.git.is_repo() and self.config.get('git', 'enabled'):
            r = self.git.auto_sync()
            if r and r.get('commit'): self._log(f"📝 Git: {r['commit']['sha']}")

    def _sz(self, b):
        if b < 1024: return f"{b}B"
        if b < 1048576: return f"{b/1024:.1f}K"
        return f"{b/1048576:.1f}M"


def main(config_path='config.yaml'):
    TUI(config_path).start()

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'config.yaml')
