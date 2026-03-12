#!/usr/bin/env python3
"""
keystone_encrypt.py -- Keystone Encrypt GUI

A system-tray application that manages NFC-card-protected encrypted vaults.
Window is visible ONLY when the Keystone card is present; hides on removal.

Architecture
------------
Thread                Role
------------------------------------------------------------------
Main (Tk)             All widget operations; drains event queue every 100ms
CardMonitor (daemon)  Polls SCardGetStatusChange; posts events to _Q
Worker (transient)    PBKDF2 derivation + vault decrypt/lock; posts result
WatchdogObserver      Per-open-vault file watcher; posts sync events to _Q
Pystray (daemon)      System tray icon; posts events to _Q (thread-safe)

Thread safety: ONLY the main thread touches Tk widgets.
All other threads communicate via _Q (queue.SimpleQueue).

Security policy (password mismatch) -- see skills/security-expert.md
----------------------------------------------------------------------
- Full PBKDF2 always runs even on wrong card/password: no timing oracle
- Error message: "Wrong password or wrong card" (no specifics leaked)
- Exponential backoff after failure: 2, 4, 8, ... 64 seconds
- After 10 attempts: 15-minute vault lockout; counter persisted to disk
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Optional

# ── Path setup: DEMO/ lives one level below the package root ──────────────────
# ── Path setup: library/ is alongside demo/ ───────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "library"))

# ── Dependencies ──────────────────────────────────────────────────────────────
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from cryptography.exceptions import InvalidTag  # type: ignore
except ImportError:
    print('[ERROR] cryptography not found. Run: pip install cryptography')
    sys.exit(1)

try:
    import pystray  # type: ignore
    from PIL import Image, ImageDraw  # type: ignore
except ImportError:
    print('[ERROR] pystray or Pillow not found. Run: pip install pystray Pillow')
    sys.exit(1)

try:
    from keystone_nfc import CardInfo, KeystoneReader  # type: ignore
    from keystone_nfc.exceptions import NoReaderError  # type: ignore
    from keystone_nfc.registry import VaultEntry, VaultRegistry  # type: ignore
    from keystone_nfc.watcher import VaultWatcher  # type: ignore
except ImportError as e:
    print(f'[ERROR] keystone_nfc not found ({e}). Ensure {_ROOT} is on PYTHONPATH.')
    sys.exit(1)

from folder_lock import (  # type: ignore
    decrypt_vault,
    delete_enc_for_path,
    derive_key,
    encrypt_one_file,
    encrypt_workdir,
    move_enc_for_path,
)

log = logging.getLogger('keystone_gui')

# ── Event queue (module-level, shared across all threads) ─────────────────────
_Q: queue.SimpleQueue = queue.SimpleQueue()

# ── Security policy ───────────────────────────────────────────────────────────

_ATTEMPTS_FILE = Path.home() / '.keystone' / 'attempts.json'
_MAX_ATTEMPTS  = 10
_MAX_BACKOFF   = 64    # seconds before capping (then 15-min lockout)
_LOCKOUT_SECS  = 900   # 15 minutes after max attempts


def _load_attempts() -> dict:
    try:
        return json.loads(_ATTEMPTS_FILE.read_text('utf-8'))
    except Exception:
        return {}


def _save_attempts(data: dict) -> None:
    _ATTEMPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ATTEMPTS_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')


class SecurityPolicy:
    """Per-vault attempt tracking: exponential backoff + lockout after max tries."""

    def __init__(self, vault_id: str) -> None:
        self._id   = vault_id
        self._data = _load_attempts().get(vault_id, {'attempts': 0, 'locked_until': 0})

    def reload(self) -> None:
        self._data = _load_attempts().get(self._id, {'attempts': 0, 'locked_until': 0})

    def is_locked_out(self) -> bool:
        return time.time() < self._data.get('locked_until', 0)

    def seconds_remaining(self) -> int:
        return max(0, int(self._data.get('locked_until', 0) - time.time()))

    def record_failure(self) -> None:
        n = self._data.get('attempts', 0) + 1
        self._data['attempts'] = n
        if n >= _MAX_ATTEMPTS:
            self._data['locked_until'] = time.time() + _LOCKOUT_SECS
        else:
            self._data['locked_until'] = time.time() + min(2 ** (n - 1), _MAX_BACKOFF)
        all_data = _load_attempts()
        all_data[self._id] = self._data
        _save_attempts(all_data)

    def record_success(self) -> None:
        all_data = _load_attempts()
        all_data.pop(self._id, None)
        _save_attempts(all_data)
        self._data = {'attempts': 0, 'locked_until': 0}


# ── Tray icon image ───────────────────────────────────────────────────────────

def _make_tray_image(locked: bool) -> Image.Image:
    img   = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    draw  = ImageDraw.Draw(img)
    color = (200, 70, 70, 255) if locked else (70, 200, 100, 255)
    draw.rounded_rectangle([8, 8, 56, 56], radius=10, fill=color)
    draw.text((20, 20), 'K', fill=(255, 255, 255, 255))
    return img


# ── Password dialog ───────────────────────────────────────────────────────────

class _PasswordDialog(tk.Toplevel):
    """Modal vault-unlock password prompt. Sets self.result to password or None."""

    def __init__(self, parent: tk.Misc, vault_name: str, card_uid: str) -> None:
        super().__init__(parent)
        self.title('Unlock Vault')
        self.resizable(False, False)
        self.grab_set()
        self.result: Optional[str] = None

        ttk.Label(self, text=vault_name, font=('', 12, 'bold')).pack(pady=(16, 2), padx=24)
        ttk.Label(self, text=f'Card: {card_uid}', foreground='#888').pack(padx=24)
        ttk.Separator(self, orient='horizontal').pack(fill='x', padx=12, pady=8)

        ttk.Label(self, text='Password:').pack(anchor='w', padx=24)
        self._pw = ttk.Entry(self, show='*', width=30)
        self._pw.pack(padx=24, pady=(2, 12))
        self._pw.focus_set()

        bf = ttk.Frame(self)
        bf.pack(pady=(0, 14))
        ttk.Button(bf, text='Unlock', command=self._ok, width=10).pack(side='left', padx=4)
        ttk.Button(bf, text='Cancel', command=self._cancel, width=10).pack(side='left', padx=4)

        self.bind('<Return>', lambda _: self._ok())
        self.bind('<Escape>', lambda _: self._cancel())
        self.protocol('WM_DELETE_WINDOW', self._cancel)
        self.wait_window()

    def _ok(self) -> None:
        self.result = self._pw.get()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


# ── Add vault dialog ──────────────────────────────────────────────────────────

class _AddVaultDialog(tk.Toplevel):
    """Dialog for registering a new vault path."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title('Add Vault')
        self.resizable(False, False)
        self.grab_set()
        self.result: Optional[tuple[str, Path, Optional[Path]]] = None  # (name: str, vault: Path, workdir: Path|None)

        ttk.Label(self, text='Vault name:').grid(row=0, column=0, sticky='w', padx=12, pady=4)
        self._name = ttk.Entry(self, width=32)
        self._name.grid(row=0, column=1, padx=(0, 4), pady=4)

        ttk.Label(self, text='Vault folder:').grid(row=1, column=0, sticky='w', padx=12, pady=4)
        self._vault = ttk.Entry(self, width=32)
        self._vault.grid(row=1, column=1, padx=(0, 4))
        ttk.Button(self, text='...', width=3,
                   command=self._browse_vault).grid(row=1, column=2, padx=(0, 8))

        ttk.Label(self, text='Workdir (optional):').grid(row=2, column=0, sticky='w', padx=12, pady=4)
        self._work = ttk.Entry(self, width=32)
        self._work.grid(row=2, column=1, padx=(0, 4))
        ttk.Button(self, text='...', width=3,
                   command=self._browse_work).grid(row=2, column=2, padx=(0, 8))

        ttk.Label(self, text='Leave blank to use vault/.working/',
                  foreground='#888', font=('', 8)).grid(row=3, column=1, sticky='w')

        bf = ttk.Frame(self)
        bf.grid(row=4, column=0, columnspan=3, pady=12)
        ttk.Button(bf, text='Add', command=self._ok, width=10).pack(side='left', padx=4)
        ttk.Button(bf, text='Cancel', command=self._cancel, width=10).pack(side='left', padx=4)

        self.bind('<Return>', lambda _: self._ok())
        self.bind('<Escape>', lambda _: self._cancel())
        self.protocol('WM_DELETE_WINDOW', self._cancel)
        self.wait_window()

    def _browse_vault(self) -> None:
        d = filedialog.askdirectory(title='Select vault directory', parent=self)
        if d:
            self._vault.delete(0, 'end')
            self._vault.insert(0, d)

    def _browse_work(self) -> None:
        d = filedialog.askdirectory(title='Select workdir (or leave blank)', parent=self)
        if d:
            self._work.delete(0, 'end')
            self._work.insert(0, d)

    def _ok(self) -> None:
        name  = self._name.get().strip()
        vault = self._vault.get().strip()
        if not name or not vault:
            messagebox.showwarning('Missing fields', 'Name and vault folder are required.',
                                   parent=self)
            return
        work = self._work.get().strip()
        self.result = (name, Path(vault), Path(work) if work else None)
        self.destroy()

    def _cancel(self) -> None:
        self.destroy()


# ── Main application ──────────────────────────────────────────────────────────

# State stored per open vault
_VaultState = Dict  # keys: key (bytes), workdir (Path), watcher (VaultWatcher)

STATUS_COLOR = {
    'open':      '#00aa55',
    'locked':    '#cc8800',
    'empty':     '#888888',
    'not_found': '#cc3333',
}
STATUS_LABEL = {
    'open':      'OPEN',
    'locked':    'LOCKED',
    'empty':     'EMPTY',
    'not_found': 'NOT FOUND',
}


class App:
    """Keystone Encrypt — main application class."""

    def __init__(self) -> None:
        self.root: tk.Tk = None  # type: ignore
        self._status_var: tk.StringVar = None  # type: ignore
        self._vault_frame: ttk.Frame = None  # type: ignore
        
        self._registry = VaultRegistry()
        self._card:    Optional[CardInfo]  = None
        self._open:    Dict[str, _VaultState]         = {}
        self._workers: Dict[str, threading.Thread]    = {}
        self._tray:    Optional[pystray.Icon]         = None
        self._monitor: Optional[KeystoneReader]       = None

        self._build_window()
        self._start_card_monitor()
        self._start_tray()

    # ── Window construction ────────────────────────────────────────────────────

    def _build_window(self) -> None:
        self.root = tk.Tk()
        self.root.title('Keystone Encrypt')
        self.root.resizable(False, False)
        self.root.protocol('WM_DELETE_WINDOW', self._hide_window)

        self._status_var = tk.StringVar(value='Waiting for NFC card...')
        ttk.Label(self.root, textvariable=self._status_var,
                  font=('', 9), foreground='#666').pack(padx=16, pady=(10, 0), anchor='w')

        lf = ttk.LabelFrame(self.root, text='Vaults', padding=8)
        lf.pack(fill='both', expand=True, padx=12, pady=8)
        self._vault_frame = ttk.Frame(lf)
        self._vault_frame.pack(fill='both', expand=True)

        bf = ttk.Frame(self.root)
        bf.pack(pady=(0, 10), padx=12, fill='x')
        ttk.Button(bf, text='+ Add Vault', command=self._add_vault).pack(side='left')
        ttk.Button(bf, text='Refresh',     command=self._refresh_list).pack(side='left', padx=4)

        self._refresh_list()
        self.root.after(100, lambda: self._pump_queue())  # type: ignore

    def _hide_window(self) -> None:
        self.root.withdraw()

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._refresh_list()

    def _set_status(self, msg: str) -> None:
        self._status_var.set(msg)

    # ── Vault list ─────────────────────────────────────────────────────────────

    def _refresh_list(self) -> None:
        for w in self._vault_frame.winfo_children():
            w.destroy()
        vaults = self._registry.all()
        if not vaults:
            ttk.Label(self._vault_frame,
                      text='No vaults configured. Click "+ Add Vault" to start.',
                      foreground='#888').pack(pady=24)
            return
        for entry in vaults:
            self._make_vault_row(entry)

    def _make_vault_row(self, entry: VaultEntry) -> None:
        is_open = entry.id in self._open
        status  = 'open' if is_open else entry.status()
        color   = STATUS_COLOR.get(status, '#888')
        label   = STATUS_LABEL.get(status, status)

        row = ttk.Frame(self._vault_frame, relief='solid', padding=(8, 6))
        row.pack(fill='x', pady=2)

        name_lbl = ttk.Label(row, text=entry.name, font=('', 10, 'bold'), width=22, anchor='w')
        name_lbl.pack(side='left')
        if is_open:
            name_lbl.configure(foreground='#0066cc', cursor='hand2')
            name_lbl.bind('<Button-1>', lambda _e, e=entry: self._open_folder(e))  # type: ignore
        ttk.Label(row, text=label, foreground=color, width=10).pack(side='left')

        if is_open:
            ttk.Button(row, text='Open Folder',
                       command=lambda e=entry: self._open_folder(e)).pack(side='left', padx=2)  # type: ignore
            ttk.Button(row, text='Lock',
                       command=lambda e=entry: self._request_lock(e)).pack(side='left', padx=2)  # type: ignore
        elif status == 'locked':
            if self._card:
                ttk.Button(row, text='Unlock',
                           command=lambda e=entry: self._ask_password_and_unlock(e)).pack(side='left', padx=2)  # type: ignore
            else:
                ttk.Label(row, text='(insert card to unlock)',
                          foreground='#888', font=('', 8)).pack(side='left', padx=6)

        ttk.Button(row, text='Remove',
                   command=lambda e=entry: self._remove_vault(e)).pack(side='right')  # type: ignore

    # ── Vault management ───────────────────────────────────────────────────────

    def _add_vault(self) -> None:
        dlg = _AddVaultDialog(self.root)
        result = dlg.result
        if not result:
            return
        name, vault_path, workdir_path = result
        if not vault_path.is_dir():
            messagebox.showerror('Error', f'Directory not found:\n{vault_path}',
                                 parent=self.root)
            return
        self._registry.add(name, vault_path, workdir_path)
        self._refresh_list()

    def _remove_vault(self, entry: VaultEntry) -> None:
        if entry.id in self._open:
            messagebox.showwarning('Vault open',
                                   f'Lock "{entry.name}" before removing it.',
                                   parent=self.root)
            return
        if messagebox.askyesno('Remove Vault',
                               f'Remove "{entry.name}" from registry?\n'
                               '(Encrypted files are NOT deleted.)',
                               parent=self.root):
            self._registry.remove(entry.id)
            self._refresh_list()

    def _open_folder(self, entry: VaultEntry) -> None:
        state = self._open.get(entry.id)
        if not state:
            return
        workdir: Path = state['workdir']
        workdir.mkdir(parents=True, exist_ok=True)
        if sys.platform == 'win32':
            os.startfile(str(workdir))
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(workdir)])
        else:
            subprocess.Popen(['xdg-open', str(workdir)])

    # ── Unlock flow ────────────────────────────────────────────────────────────

    def _ask_password_and_unlock(self, entry: VaultEntry) -> None:
        card = self._card
        if not card:
            messagebox.showinfo('Card needed', 'Insert your Keystone card first.',
                                parent=self.root)
            return

        policy = SecurityPolicy(entry.id)
        policy.reload()
        if policy.is_locked_out():
            secs = policy.seconds_remaining()
            messagebox.showwarning('Too many attempts',
                                   f'Vault locked out.\nTry again in {secs} seconds.',
                                   parent=self.root)
            return

        dlg = _PasswordDialog(self.root, entry.name, card.uid_hex)
        if dlg.result is None:
            return   # user cancelled

        self._set_status(f'Unlocking "{entry.name}"...')
        self._spawn_worker(entry.id, self._worker_unlock,
                           entry, dlg.result, self._card, policy)

    def _request_lock(self, entry: VaultEntry) -> None:
        state = self._open.get(entry.id)
        if not state:
            return
        self._set_status(f'Locking "{entry.name}"...')
        self._spawn_worker(entry.id, self._worker_lock, entry, state)

    # ── Worker threads ─────────────────────────────────────────────────────────

    def _spawn_worker(self, vault_id: str, fn, *args) -> None:
        existing = self._workers.get(vault_id)
        if existing and existing.is_alive():
            return
        t = threading.Thread(target=fn, args=args, daemon=True,
                             name=f'worker-{vault_id[:8]}')  # type: ignore
        self._workers[vault_id] = t
        t.start()

    def _worker_unlock(self, entry: VaultEntry, password: str,
                       card: CardInfo, policy: SecurityPolicy) -> None:
        """Worker: derive key + decrypt vault. Always runs full PBKDF2."""
        key     = derive_key(password, card.uid_bytes)
        workdir = entry.workdir
        try:
            count = decrypt_vault(entry.vault, workdir, key)
            policy.record_success()
            _Q.put(('unlock_done', entry.id, key, workdir, count))
        except (ValueError, InvalidTag):
            policy.record_failure()
            _Q.put(('unlock_fail', entry.id, policy.seconds_remaining()))

    def _worker_lock(self, entry: VaultEntry, state: _VaultState) -> None:
        """Worker: stop watcher, encrypt workdir -> vault, wipe workdir."""
        watcher = state.get('watcher')
        if watcher:
            watcher.stop()
        try:
            count = encrypt_workdir(entry.vault, state['workdir'], state['key'])
            _Q.put(('lock_done', entry.id, count))
        except Exception as exc:
            _Q.put(('lock_fail', entry.id, str(exc)))

    # ── VaultWatcher callbacks (watchdog thread -> queue) ─────────────────────

    def _watcher_encrypt(self, vault_id: str, rel: str, content: bytes) -> None:
        state = self._open.get(vault_id)
        if not state:
            return
        try:
            entry = self._registry.get(vault_id)
            encrypt_one_file(entry.vault, rel, content, state['key'])
            _Q.put(('sync', vault_id, 'encrypt', rel))
        except Exception as e:
            log.error('[watcher] encrypt %s: %s', rel, e)

    def _watcher_delete(self, vault_id: str, rel: str) -> None:
        state = self._open.get(vault_id)
        if not state:
            return
        try:
            entry = self._registry.get(vault_id)
            delete_enc_for_path(entry.vault, rel, state['key'])
            _Q.put(('sync', vault_id, 'delete', rel))
        except Exception as e:
            log.error('[watcher] delete %s: %s', rel, e)

    def _watcher_move(self, vault_id: str, old: str, new: str, content: bytes) -> None:
        state = self._open.get(vault_id)
        if not state:
            return
        try:
            entry = self._registry.get(vault_id)
            move_enc_for_path(entry.vault, old, new, content, state['key'])
            _Q.put(('sync', vault_id, 'move', f'{old} -> {new}'))
        except Exception as e:
            log.error('[watcher] move %s->%s: %s', old, new, e)

    # ── Queue pump (main thread only) ─────────────────────────────────────────

    def _pump_queue(self) -> None:
        try:
            while True:
                self._dispatch(_Q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, lambda: self._pump_queue())  # type: ignore

    def _dispatch(self, event: tuple) -> None:
        kind = event[0]

        if kind == 'card_inserted':
            card: CardInfo = event[1]  # type: ignore
            self._card = card
            self._set_status(f'Card: {card.uid_hex}')
            tray = self._tray
            if tray:
                tray.icon = _make_tray_image(locked=False)
            self._show_window()

        elif kind == 'card_removed':
            self._card = None
            self._set_status('Card removed — locking all vaults...')
            tray = self._tray
            if tray:
                tray.icon = _make_tray_image(locked=True)
            self._lock_all()
            self.root.after(800, lambda: self._hide_window())  # type: ignore

        elif kind == 'monitor_error':
            log.warning('Card monitor: %s', event[1])

        elif kind == 'unlock_done':
            _, vault_id, key, workdir, count = event
            entry = self._registry.get(vault_id)
            watcher = VaultWatcher(
                workdir=workdir,
                on_encrypt=lambda r, c, v=vault_id: self._watcher_encrypt(v, r, c),
                on_delete=lambda r,    v=vault_id: self._watcher_delete(v, r),
                on_move=lambda o, n, c, v=vault_id: self._watcher_move(v, o, n, c),
            )
            watcher.start()
            self._open[vault_id] = {'key': key, 'workdir': workdir, 'watcher': watcher}
            name = entry.name if entry else vault_id
            self._set_status(f'Unlocked "{name}" ({count} file(s))')
            self._refresh_list()

        elif kind == 'unlock_fail':
            _, vault_id, wait_secs = event
            entry = self._registry.get(vault_id)
            msg = 'Wrong password or wrong card.'
            if wait_secs:
                msg += f'\nWait {wait_secs}s before next attempt.'
            self._set_status('Unlock failed.')
            messagebox.showerror('Unlock Failed', msg, parent=self.root)

        elif kind == 'lock_done':
            _, vault_id, count = event
            entry = self._registry.get(vault_id)
            self._open.pop(vault_id, None)
            name = entry.name if entry else vault_id
            self._set_status(f'Locked "{name}" ({count} file(s) encrypted)')
            self._refresh_list()

        elif kind == 'lock_fail':
            _, vault_id, msg = event
            log.error('Lock failed %s: %s', vault_id, msg)

        elif kind == 'sync':
            log.debug('[sync:%s] %s: %s', event[2], event[1], event[3])

        elif kind == 'tray_show':
            self._show_window()

        elif kind == 'tray_quit':
            self._shutdown()

    # ── Card-removal: lock all open vaults ────────────────────────────────────

    def _lock_all(self) -> None:
        for vault_id, state in list(self._open.items()):
            entry = self._registry.get(vault_id)
            if entry:
                self._spawn_worker(vault_id, self._worker_lock, entry, state)

    # ── Card monitor ──────────────────────────────────────────────────────────

    def _start_card_monitor(self) -> None:
        try:
            monitor = KeystoneReader()
            self._monitor = monitor

            @monitor.on_card_inserted
            def _on_insert(card: CardInfo):
                _Q.put(('card_inserted', card))

            @monitor.on_card_removed
            def _on_remove():
                _Q.put(('card_removed',))

            @monitor.on_error
            def _on_err(exc):
                _Q.put(('monitor_error', exc))

            monitor.start()
        except NoReaderError as e:
            log.warning('No NFC reader at startup: %s', e)
            self._set_status('No NFC reader detected.')

    # ── System tray ───────────────────────────────────────────────────────────

    def _start_tray(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem('Show',  lambda *_: _Q.put(('tray_show',))),
            pystray.MenuItem('Quit',  lambda *_: _Q.put(('tray_quit',))),
        )
        self._tray = pystray.Icon(
            'keystone',
            _make_tray_image(locked=True),
            'Keystone Encrypt',
            menu,
        )
        tray = self._tray
        if tray:
            threading.Thread(target=tray.run, daemon=True,
                             name='pystray').start()

    # ── Graceful shutdown ─────────────────────────────────────────────────────

    def _shutdown(self) -> None:
        """Stop monitor, lock all open vaults synchronously, then quit."""
        monitor = self._monitor
        if monitor:
            monitor.stop()

        for vault_id, state in list(self._open.items()):
            entry   = self._registry.get(vault_id)
            watcher = state.get('watcher')
            if watcher:
                watcher.stop()
            if entry:
                try:
                    encrypt_workdir(entry.vault, state['workdir'], state['key'])
                except Exception as exc:
                    log.error('Shutdown lock failed for %s: %s', vault_id, exc)

        tray = self._tray
        if tray:
            tray.stop()
        self.root.destroy()

    # ── Run ───────────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Start the application. Window is hidden until card is present."""
        self.root.withdraw()
        self.root.mainloop()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )
    App().run()


if __name__ == '__main__':
    main()
