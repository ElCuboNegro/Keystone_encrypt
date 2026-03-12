---
id: ADR-D004
title: Experiments run in Docker containers
status: Accepted
date: 2026-03-11
---

# ADR-D004: Experiments run in Docker containers

## Status
Accepted

## Context
Code experiments written to probe or understand behavior (e.g., testing PC/SC call sequences,
verifying encryption roundtrips, checking library behavior) must not modify the host OS.
Packages installed during experiments should not pollute the development environment.

## Decision
All experiment scripts run inside Docker containers defined in `DEMO/docker/`.
Results are captured via volume mounts to `experiments/` (read by host, written by container).

## Justification
- Container provides reproducible, clean Python environment for each experiment
- Failed experiments (crashing scripts, bad pip installs) cannot affect host
- Results are portable: anyone can reproduce the experiment from the Dockerfile alone
- NFC hardware access: if needed, pass `--device /dev/bus/usb` (Linux) or run natively (Windows)

## Consequences
- Windows Docker Desktop required for local runs on Windows
- Hardware-level experiments (SCard, USB HID) require native access — use `--net=host` + device passthrough or run natively with a note in the experiment file
- Each experiment script must document its own Docker run command in its header
