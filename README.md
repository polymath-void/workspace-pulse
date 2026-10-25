# Pulse — Workspace Pulse UI sidecar

A Termux/Android **auxiliary-pane UI plugin** for Antigravity. It renders a live
dashboard of the machine and the NodeOS AST graph inside the side pane, and
ships a clickable `sidecar://` pill so it can be opened straight from chat.

```
plugins/pulse/
├── plugin.json                    # plugin manifest (discovery marker)
├── README.md                      # this file
├── pulse_cli.py                   # terminal CLI driver (linked to $PREFIX/bin/pulse)
└── sidecars/
    └── workspace/                 # sidecar folder -> sidecar id `pulse/workspace`
        ├── sidecar.json           # runtime config: command, has_web_ui, ui_config
        ├── server.py              # zero-dependency Python 3 backend
        ├── terminal.py            # ANSI terminal rendering engine
        └── index.html             # the AUX_PANE panel (offline, theme-aware)
```

## What the panel shows

| Card | Source | Notes |
| :--- | :--- | :--- |
| Memory | `/proc/meminfo` | used / free, swap, usage bar |
| Disk (`$HOME`) | `os.statvfs` | free / total, usage bar |
| Load average | `/proc/loadavg` | blocked on some Android builds → shows `n/a` |
| Uptime | `/proc/uptime` | blocked on some Android builds → shows `—` |
| NodeOS AST graph | `agy_nodeos.db` (read-only) | daemon status, node/edge counts, node-type and per-project breakdowns, recently indexed symbols |
| Boot daemon | `nodeos -d` | the only mutating action; token-guarded POST |

## Runtime contract

The Antigravity sidecar runtime injects:

- `ANTIGRAVITY_SIDECAR_WEB_PORT` — the loopback port to bind (`127.0.0.1` only).
- `ANTIGRAVITY_SIDECAR_UI_TOKEN` — required on every non-`GET` request
  (`X-Sidecar-Token` header, or `?token=` on the URL).

Endpoints:

| Method | Path | Purpose |
| :--- | :--- | :--- |
| `GET` | `/auxpane` (and `/`) | the panel HTML |
| `GET` | `/api/pulse` | aggregated JSON snapshot |
| `GET` | `/health` | liveness probe |
| `POST` | `/api/nodeos/boot` | start the NodeOS daemon (token required) |

## Design notes

- **Python, not Node.** This environment has no `node` binary, and the workspace
  rules prefer native Python/Node over proot binaries. The backend is stdlib
  only — no pip installs, no build step.
- **Richest-graph selection.** Several `agy_nodeos.db` files can exist (the
  umbrella root and each sub-repo attach their own). Instead of binding to the
  one nearest the sidecar's CWD, the backend probes a bounded candidate list and
  keeps the graph with the most nodes — the umbrella root wins when indexed.
  Override with `PULSE_NODEOS_DB=/abs/path/agy_nodeos.db`.
- **Bounded work.** No directory walks and no unbounded scans; discovery is a
  fixed candidate list. The daemon probe is cached for 20s because `nodeos` is a
  Python CLI (~300ms interpreter startup per call).
- **Never pollute the plugin folder.** NodeOS binds its state artifacts
  (`agy_nodeos.db`, `daemon.log`, `workflow.json`, `.agents/`, `.jsagent/`) to
  its *current working directory*. Both the daemon probe and the boot action are
  therefore launched with `cwd` pinned to the resolved workspace root, never the
  sidecar's own folder.
- **Degrade, never crash.** Every collector is wrapped; missing Android-restricted
  counters become `null` and surface as `n/a` in the UI.

## Terminal View & CLI

A terminal-native interface is available directly via the `pulse` CLI command:

```bash
pulse              # Instant snapshot terminal dashboard
pulse -w           # Continuous live monitor (refreshes every 2s)
pulse --boot       # Boot NodeOS daemon from terminal
pulse --web        # Launch web dashboard in mobile browser
curl -s 127.0.0.1:7890/  # Query terminal view via curl
```

## Open it (Web UI)

Click the pill, or toggle the pane from the Automations/Sidecars dashboard:

[Workspace Pulse](sidecar://pulse/workspace/auxpane)

## Alternative packaging (standalone sidecar)

If your build does not load sidecars from plugin folders, the same folder works
as a plain global sidecar — move (do not symlink) it to the standalone root and
the sidecar id becomes just `workspace`:

```bash
mv ~/.gemini/config/plugins/pulse/sidecars/workspace \
   ~/.gemini/config/sidecars/workspace
```

## Manual smoke test

```bash
cd ~/.gemini/config/plugins/pulse/sidecars/workspace
PORT=$(python3 -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()")
ANTIGRAVITY_SIDECAR_WEB_PORT=$PORT python3 server.py &
curl -s "http://127.0.0.1:$PORT/health"
curl -s "http://127.0.0.1:$PORT/api/pulse" | python3 -m json.tool | head -30
```
