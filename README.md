# Keystone Encrypt

NFC-card-protected encrypted vault manager for Windows.

Insert your Keystone smart card → vault unlocks. Remove it → vault locks and encrypts automatically.

Built on [`keystone-nfc`](https://github.com/ElCuboNegro/Keystone) — the PC/SC NFC card library.

---

## Included applications

### `keystone_encrypt.py` — Vault GUI

A system-tray application that manages NFC-card-protected encrypted vaults.

**Features:**
- **Presence-driven UI** — window hides on startup, reveals only when a valid Keystone card is present
- **Asynchronous / event-driven** — multi-threaded; the Tkinter GUI thread never blocks while PC/SC monitors hardware
- **Real-time sync** — `watchdog` auto-encrypts files added or modified in the vault working directory while unlocked
- **AES-GCM + PBKDF2** — 600k iteration key derivation; HMAC-SHA256 deterministic encrypted filenames
- **Brute-force protection** — exponential backoff, max 10 attempts then 15-minute lockout

### `nfc_reader_demo.py` — NFC reader CLI

Minimal CLI demonstrating the `keystone_nfc.KeystoneReader` API.

```
python nfc_reader_demo.py              # continuous event loop
python nfc_reader_demo.py --once       # read one card and exit
python nfc_reader_demo.py --list-readers
```

---

## Setup

```bash
# Install all dependencies (library + GUI deps)
pip install -r requirements.txt

# Run the vault GUI
python keystone_encrypt.py

# Run the NFC monitor
python nfc_reader_demo.py
```

> **Note:** The GUI window is hidden on start. Insert your Keystone NFC card to reveal it.

### Requirements

- Windows 10/11 with NFC reader (built-in NxpNfc / Microsoft IFD, or ACR122U)
- Python 3.11+
- `pywin32` — for real-time card-removal detection via WMI (installed automatically on Windows)

---

## Architecture

```
keystone_encrypt.py
    └── keystone_nfc.KeystoneReader   ← NFC card events (PC/SC)
    └── folder_lock                   ← AES-GCM vault encryption
    └── watchdog                      ← real-time file-system sync
    └── pystray / tkinter             ← system tray + GUI
```

Key implementation patterns:
- `App._start_card_monitor()` — standard pattern for `@reader.on_card_inserted` / `@reader.on_card_removed`
- `queue.SimpleQueue` (`_Q`) — thread-safe message passing from PC/SC / watchdog threads to the GUI thread

---

## Architectural Decision Records

All design decisions are documented in [`docs/adr/`](docs/adr/).

---

## Related

- [`ElCuboNegro/Keystone`](https://github.com/ElCuboNegro/Keystone) — monorepo: NFC library, reverse-engineering research, and hardware experiments
