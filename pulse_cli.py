#!/data/data/com.termux/files/usr/bin/python3
"""Workspace Pulse CLI - Terminal Dashboard & Controller.

Usage:
  pulse                 Render terminal dashboard snapshot
  pulse -w, --watch     Live continuous dashboard (refreshes every 2s)
  pulse --web           Open web dashboard in Android browser
  pulse --boot          Start/boot the NodeOS daemon (nodeos -d)
  pulse --json          Output snapshot as JSON
  pulse --serve         Ensure background HTTP server is running
  pulse -h, --help      Show this help message
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

REAL_FILE = os.path.realpath(__file__)
HERE = os.path.dirname(REAL_FILE)
SIDECAR_DIR = os.path.join(HERE, "sidecars", "workspace")
if SIDECAR_DIR not in sys.path:
    sys.path.insert(0, SIDECAR_DIR)

from terminal import render_terminal
import server

DEFAULT_PORT = 7890
API_URL = f"http://127.0.0.1:{DEFAULT_PORT}/api/pulse"
HEALTH_URL = f"http://127.0.0.1:{DEFAULT_PORT}/health"
VIEW_URL = f"http://127.0.0.1:{DEFAULT_PORT}/auxpane"


def get_data():
    """Fetch from running daemon, or fallback to direct collector execution."""
    try:
        req = urllib.request.Request(API_URL, headers={"User-Agent": "pulse-cli"})
        with urllib.request.urlopen(req, timeout=0.5) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass

    # Direct in-process fallback
    return {
        "ts": int(time.time()),
        "system": server.collect_system(),
        "nodeos": server.collect_nodeos(),
    }


def is_server_running():
    try:
        req = urllib.request.Request(HEALTH_URL, headers={"User-Agent": "pulse-cli"})
        with urllib.request.urlopen(req, timeout=0.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_server():
    if is_server_running():
        return True
    log_dir = os.path.expanduser("~/.gemini/antigravity-cli/plugin_data/pulse")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "pulse.log")
    server_script = os.path.join(SIDECAR_DIR, "server.py")
    env = os.environ.copy()
    env["ANTIGRAVITY_SIDECAR_WEB_PORT"] = str(DEFAULT_PORT)

    # Detach using setsid -f
    try:
        cmd = f"setsid -f env ANTIGRAVITY_SIDECAR_WEB_PORT={DEFAULT_PORT} python3 {server_script} > {log_file} 2>&1"
        subprocess.run(cmd, shell=True, timeout=3)
        time.sleep(0.5)
        return is_server_running()
    except Exception:
        return False


def cmd_watch():
    sys.stdout.write("\033[?25l")  # Hide cursor
    try:
        while True:
            data = get_data()
            term_str = render_terminal(data)
            # Clear screen and draw
            sys.stdout.write("\033[H\033[2J")
            sys.stdout.write(term_str + "\n")
            sys.stdout.write("\033[2m  Press Ctrl+C to exit watch mode...\033[0m\n")
            sys.stdout.flush()
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h\n")  # Show cursor
        sys.stdout.flush()


def cmd_web():
    ensure_server()
    termux_open = shutil.which("termux-open-url")
    if termux_open:
        subprocess.run([termux_open, VIEW_URL])
        print(f"Dispatched {VIEW_URL} to browser.")
    else:
        print(f"Web UI available at: {VIEW_URL}")


def cmd_boot():
    if is_server_running():
        try:
            boot_url = f"http://127.0.0.1:{DEFAULT_PORT}/api/nodeos/boot"
            req = urllib.request.Request(boot_url, data=b"{}", headers={"User-Agent": "pulse-cli"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                if res.get("ok"):
                    print("NodeOS daemon boot triggered successfully.")
                else:
                    print(f"Boot warning: {res.get('error') or res}")
                return
        except Exception:
            pass
    # In-process boot fallback
    res = server.boot_nodeos()
    if res.get("ok"):
        print("NodeOS daemon booted successfully.")
    else:
        print(f"Boot result: {res}")


def main():
    parser = argparse.ArgumentParser(
        prog="pulse",
        description="Workspace Pulse - Live State & NodeOS AST Graph Terminal Cockpit",
        add_help=False,
    )
    parser.add_argument("-w", "--watch", action="store_true", help="Live continuous monitoring")
    parser.add_argument("--web", action="store_true", help="Open web UI in browser")
    parser.add_argument("--boot", action="store_true", help="Boot NodeOS daemon")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON")
    parser.add_argument("--serve", action="store_true", help="Ensure background server is running")
    parser.add_argument("-h", "--help", action="store_true", help="Show this help message")

    args = parser.parse_args()

    if args.help:
        print(__doc__.strip())
        return

    if args.web:
        cmd_web()
        return

    if args.boot:
        cmd_boot()
        return

    if args.serve:
        ok = ensure_server()
        print("Pulse server is running on http://127.0.0.1:7890/auxpane" if ok else "Failed to start server.")
        return

    if args.json:
        data = get_data()
        print(json.dumps(data, indent=2))
        return

    if args.watch:
        cmd_watch()
        return

    # Default: snapshot
    data = get_data()
    print(render_terminal(data))


if __name__ == "__main__":
    main()
