---
id: ADR-D002
title: Thread safety via SimpleQueue + root.after
status: Accepted
date: 2026-03-11
---

# ADR-D002: Thread safety via SimpleQueue + root.after

## Status
Accepted

## Context
The GUI has multiple background threads (CardMonitor, Worker, WatchdogObserver, pystray) that
produce events. tkinter is not thread-safe — calling widget methods from any non-main thread
causes crashes or silent corruption on Windows.

## Decision
Use a module-level `queue.SimpleQueue` (`_Q`) as the single inter-thread channel.
Background threads call `_Q.put(event_tuple)` only.
The main thread drains the queue every 100ms via `root.after(100, _pump_queue)`.

## Justification
- SimpleQueue has no `maxsize` — producers never block (critical for watchdog callbacks)
- 100ms polling is imperceptible to users and keeps CPU near zero when idle
- All widget mutation code lives in one place (`_dispatch`) — easy to audit
- Pattern is stdlib-only, no additional dependencies

## Consequences
- Event delivery has up to 100ms latency — acceptable for vault management UX
- All event handlers in `_dispatch` must complete quickly (no blocking calls)
- Workers that need to report results back do so via `_Q.put` after their work is done
