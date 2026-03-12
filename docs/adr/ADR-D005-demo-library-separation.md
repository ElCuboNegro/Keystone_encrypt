---
id: ADR-D005
title: Demos and library outputs are separate layers
status: Accepted
date: 2026-03-11
---

# ADR-D005: Demos and library outputs are separate layers

## Status
Accepted

## Context
The project produces two kinds of artifacts:
1. **Library outputs** — reusable packages and CLIs that ARE the archeology result (e.g., `keystone_nfc/`, `folder_lock.py`)
2. **Demo applications** — programs that USE those libraries to show they work end-to-end (e.g., `keystone_gui.py`)

Mixing these two layers in the same directory makes it hard to understand what is a building
block vs. what is a showcase.

## Decision
- **Libraries and CLI tools** live at the repo root (or under `src/` if the project grows)
- **Demo applications** live under `DEMO/`
- `DEMO/` has its own `README.md`, `docs/adr/`, and requirements
- `DEMO/` imports from the root packages via `sys.path` insertion or a proper install

## Justification
- Clear separation of concerns: someone studying the library does not need to read the demo code
- ADR trail is scoped: library ADRs in `/docs/adr/`, demo ADRs in `/DEMO/docs/adr/`
- Matches the project mandate: "DONT MIX OUTPUTS (like libraries) WITH DEMOS"

## Consequences
- Demo files use `sys.path.insert(0, str(Path(__file__).parent.parent))` to find the library
- If the project is packaged (wheel), demos should depend on the installed package instead
- Each layer must have its own ADR index — cross-references are allowed but explicit
