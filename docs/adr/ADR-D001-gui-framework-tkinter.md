---
id: ADR-D001
title: Use tkinter + pystray for GUI
status: Accepted
date: 2026-03-11
---

# ADR-D001: Use tkinter + pystray for GUI

## Status
Accepted

## Context
The vault manager needs a desktop GUI that: runs on Windows (primary target), stays resident in
the system tray, shows/hides based on card presence, and has no additional runtime dependencies
beyond what the crypto stack already requires.

## Decision
Use **tkinter** (stdlib) for the main window and **pystray + Pillow** for the system tray icon.

## Justification
- tkinter ships with CPython — zero additional install for the window itself
- pystray is the de-facto cross-platform tray library (Windows/macOS/Linux)
- No Electron / Qt / wx runtime needed — minimal footprint
- System tray pattern matches the UX goal: "program disappears when card is absent"

## Consequences
- UI is functional but visually plain (acceptable for a demo/utility)
- pystray + Pillow must be installed (`pip install pystray Pillow`)
- All widget operations MUST occur on the main thread (see ADR-D002)
