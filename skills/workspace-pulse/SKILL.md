---
name: workspace-pulse
description: Live workspace telemetry and health dashboard for Antigravity. Use when the user asks about system resources (RAM, swap, disk, battery), wants to check workspace AST symbols/projects, or requests a live status HUD in the auxiliary pane or terminal.
---

# Workspace Pulse

Workspace Pulse provides real-time system resource monitoring and AST/workspace knowledge graph introspection.

## Auxiliary Pane View
When operating in an IDE environment that supports UI sidecars, surface the one-click navigation pill:
👉 [Open Workspace Pulse](sidecar://workspace-pulse/workspace/auxpane)

## Terminal View
To inspect metrics directly in the terminal, run:
- `pulse` : Instant snapshot dashboard
- `pulse -w` : Continuous live updating cockpit (refreshes every 2s)
- `pulse --boot` : Boot NodeOS AST daemon
- `pulse --web` : Launch browser dashboard

## API & Endpoints
When the sidecar server is running:
- Web UI: `http://127.0.0.1:7890/auxpane`
- Terminal curl view: `curl -s http://127.0.0.1:7890/`
- JSON metrics: `http://127.0.0.1:7890/api/pulse`
