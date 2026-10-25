#!/usr/bin/env python3
"""Workspace Pulse - Antigravity UI sidecar backend.

Zero-dependency Python 3 HTTP server backing the "Workspace Pulse" auxiliary
pane panel. It reads *local, read-only* state (Android/Termux system counters
and the NodeOS AST graph) and serves it as JSON to the panel.

Runtime contract (Antigravity sidecar runtime):
  * binds 127.0.0.1 on $ANTIGRAVITY_SIDECAR_WEB_PORT (injected by the host)
  * serves the AUX_PANE view declared in sidecar.json at VIEW_PATH
  * validates $ANTIGRAVITY_SIDECAR_UI_TOKEN on every non-GET request

Design constraints (see the workspace AGENTS.md):
  * Android/Termux: no /bin or /usr/bin, no `node` runtime -> Python stdlib only
  * no unbounded scans; every path lookup is a bounded candidate list
  * never crash the process: all handlers degrade to a JSON error field
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

try:
    from terminal import render_terminal
except ImportError:
    render_terminal = None

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(HERE, "index.html")

HOME = os.path.expanduser("~")
VIEW_PATH = "/auxpane"
TOKEN = os.environ.get("ANTIGRAVITY_SIDECAR_UI_TOKEN") or ""

# Absolute fallbacks first: the sidecar's PATH may not include $PREFIX/bin.
_TERMUX_BIN = "/data/data/com.termux/files/usr/bin"


def _resolve_bin(name):
    for cand in (os.path.join(_TERMUX_BIN, name), os.path.join(HOME, ".local/bin", name)):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return shutil.which(name) or ""


NODEOS_BIN = _resolve_bin("nodeos")

_db_cache = {"path": None, "checked_at": 0.0}
_DB_TTL = 30.0


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
def _db_candidates():
    """Bounded list of places the NodeOS graph DB may live. No directory walks."""
    out = []
    override = os.environ.get("PULSE_NODEOS_DB")
    if override:
        out.append(override)
    # Walk up from CWD (bounded), then the two well-known workspace roots.
    d = os.getcwd()
    for _ in range(8):
        out.append(os.path.join(d, "agy_nodeos.db"))
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    out.append(os.path.join(HOME, "Projects", "agy_nodeos.db"))
    out.append(os.path.join(HOME, "agy_nodeos.db"))
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _db_node_count(path):
    """Cheap probe: how many nodes does this candidate graph hold? -1 on error."""
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=3)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM nodes")
        count = cur.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return -1


def find_db():
    """Pick the richest reachable graph.

    Several `agy_nodeos.db` files can exist (the umbrella root and individual
    sub-repos each attach their own). Rather than binding to whichever one
    happens to sit closest to the sidecar's own CWD, probe every bounded
    candidate and keep the graph with the most nodes -- the umbrella root wins
    when it is indexed, a local sub-repo graph wins when it is not.
    """
    now = time.time()
    if _db_cache["path"] and now - _db_cache["checked_at"] < _DB_TTL:
        if os.path.exists(_db_cache["path"]):
            return _db_cache["path"]

    override = os.environ.get("PULSE_NODEOS_DB")
    if override and os.path.isfile(override):
        found = override
    else:
        found, best = None, -1
        for cand in _db_candidates():
            if not os.path.isfile(cand):
                continue
            count = _db_node_count(cand)
            if count > best:
                found, best = cand, count

    _db_cache.update(path=found, checked_at=now)
    return found


# --------------------------------------------------------------------------- #
# collectors
# --------------------------------------------------------------------------- #
_ping_cache = {"online": None, "at": 0.0}
_PING_TTL = 20.0


def _workspace_cwd():
    """NodeOS resolves its workspace dynamically from its CWD, so it must never
    be launched from the sidecar's own folder -- that would write
    `agy_nodeos.db`, `daemon.log`, `workflow.json`, `.agents/` and `.jsagent/`
    into the plugin directory. Always run it from the workspace root instead.
    """
    db = find_db()
    if db:
        return os.path.dirname(db)
    fallback = os.path.join(HOME, "Projects")
    return fallback if os.path.isdir(fallback) else HOME


def probe_daemon():
    """Is the NodeOS daemon alive?

    `nodeos` is a Python CLI, so every probe costs ~300ms of interpreter
    startup. Cache the verdict briefly; the boot action invalidates it.
    """
    now = time.time()
    if now - _ping_cache["at"] < _PING_TTL:
        return _ping_cache["online"]

    online = None
    if NODEOS_BIN:
        try:
            proc = subprocess.run(
                [NODEOS_BIN, "-c", "ping"],
                capture_output=True,
                text=True,
                timeout=6,
                cwd=_workspace_cwd(),
            )
            blob = ((proc.stdout or "") + (proc.stderr or "")).lower()
            online = "pong" in blob
        except Exception:
            online = None

    _ping_cache.update(online=online, at=now)
    return online


def _read_text(path):
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read()
    except Exception:
        return None


_battery_cache = {"data": None, "checked_at": 0.0}
_soc_cache = {"model": None, "cores": None}


def probe_battery():
    now = time.time()
    if now - _battery_cache["checked_at"] < 30.0:
        return _battery_cache["data"]
    try:
        bin_path = _resolve_bin("termux-battery-status")
        if bin_path:
            p = subprocess.run([bin_path], capture_output=True, text=True, timeout=2)
            if p.returncode == 0 and p.stdout.strip():
                data = json.loads(p.stdout)
                _battery_cache["data"] = data
                _battery_cache["checked_at"] = now
                return data
    except Exception:
        pass
    _battery_cache["checked_at"] = now
    return _battery_cache["data"]


def probe_soc():
    if _soc_cache["model"] is not None:
        return _soc_cache
    try:
        p = subprocess.run(["getprop", "ro.soc.model"], capture_output=True, text=True, timeout=1)
        model = p.stdout.strip()
        _soc_cache["model"] = model or "MT6886"
        _soc_cache["cores"] = os.cpu_count() or 8
    except Exception:
        _soc_cache["model"] = "MT6886"
        _soc_cache["cores"] = os.cpu_count() or 8
    return _soc_cache


def collect_system():
    out = {
        "mem_total_mb": None,
        "mem_avail_mb": None,
        "mem_used_pct": None,
        "swap_total_mb": None,
        "swap_free_mb": None,
        "disk_free_gb": None,
        "disk_total_gb": None,
        "disk_used_pct": None,
        "uptime_s": None,
        "load1": None,
        "load5": None,
        "load15": None,
        "battery_pct": None,
        "battery_temp_c": None,
        "battery_status": None,
        "cpu_model": None,
        "cpu_cores": None,
    }

    # Battery and SoC
    batt = probe_battery()
    if batt:
        out["battery_pct"] = batt.get("percentage")
        out["battery_temp_c"] = batt.get("temperature")
        out["battery_status"] = batt.get("status")

    soc = probe_soc()
    out["cpu_model"] = soc.get("model")
    out["cpu_cores"] = soc.get("cores")

    # Memory: /proc/meminfo is readable in Termux.
    mem = _read_text("/proc/meminfo")
    if mem:
        vals = {}
        for line in mem.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                vals[k.strip()] = v.strip().split()[0] if v.strip() else "0"
        try:
            total = int(vals.get("MemTotal", 0)) / 1024.0
            avail = int(vals.get("MemAvailable", 0)) / 1024.0
            swap_total = int(vals.get("SwapTotal", 0)) / 1024.0
            swap_free = int(vals.get("SwapFree", 0)) / 1024.0
            out["mem_total_mb"] = round(total, 1)
            out["mem_avail_mb"] = round(avail, 1)
            out["mem_used_pct"] = round((1 - avail / total) * 100, 1) if total else None
            out["swap_total_mb"] = round(swap_total, 1)
            out["swap_free_mb"] = round(swap_free, 1)
        except Exception:
            pass

    # Disk: cross-platform via shutil.disk_usage ($HOME).
    try:
        du = shutil.disk_usage(HOME)
        out["disk_total_gb"] = round(du.total / 1e9, 1)
        out["disk_free_gb"] = round(du.free / 1e9, 1)
        out["disk_used_pct"] = round((du.used / du.total) * 100, 1) if du.total else None
    except Exception:
        pass

    # Uptime / load: SELinux blocks these on some Android builds -> optional.
    up = _read_text("/proc/uptime")
    if up:
        try:
            out["uptime_s"] = int(float(up.split()[0]))
        except Exception:
            pass
    la = _read_text("/proc/loadavg")
    if la:
        try:
            parts = la.split()
            out["load1"], out["load5"], out["load15"] = parts[0], parts[1], parts[2]
        except Exception:
            pass

    return out


def collect_nodeos():
    info = {
        "binary": NODEOS_BIN,
        "available": bool(NODEOS_BIN),
        "online": None,
        "db_path": None,
        "db_age_s": None,
        "workspace_root": None,
        "nodes": None,
        "edges": None,
        "node_types": [],
        "projects": [],
        "recent": [],
        "error": None,
    }

    info["online"] = probe_daemon()

    db = find_db()
    if not db:
        ws_root = _workspace_cwd()
        info["workspace_root"] = ws_root
        try:
            subdirs = [
                d for d in os.listdir(ws_root)
                if os.path.isdir(os.path.join(ws_root, d)) and not d.startswith(".")
            ]
            info["projects"] = [{"name": d, "count": 1} for d in sorted(subdirs)[:10]]
        except Exception:
            pass
        try:
            p = subprocess.run(["git", "branch", "--show-current"], cwd=ws_root, capture_output=True, text=True, timeout=1)
            b = p.stdout.strip()
            if b:
                info["git_branch"] = b
        except Exception:
            pass
        return info

    info["db_path"] = db
    info["workspace_root"] = os.path.dirname(db)
    try:
        info["db_age_s"] = int(time.time() - os.path.getmtime(db))
    except Exception:
        pass

    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) AS c FROM nodes")
        info["nodes"] = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM edges")
        info["edges"] = cur.fetchone()["c"]

        cur.execute(
            "SELECT node_type AS t, COUNT(*) AS c FROM nodes "
            "GROUP BY node_type ORDER BY c DESC LIMIT 6"
        )
        info["node_types"] = [{"type": r["t"], "count": r["c"]} for r in cur.fetchall()]

        root = info["workspace_root"]
        prefix = root + "/"
        cur.execute(
            "SELECT CASE "
            "  WHEN instr(substr(filepath, ?), '/') > 0 "
            "    THEN substr(substr(filepath, ?), 1, instr(substr(filepath, ?), '/') - 1) "
            "  ELSE substr(filepath, ?) END AS proj, "
            "  COUNT(*) AS c "
            "FROM nodes WHERE filepath LIKE ? "
            "GROUP BY proj ORDER BY c DESC LIMIT 10",
            (len(prefix) + 1, len(prefix) + 1, len(prefix) + 1, len(prefix) + 1, prefix + "%"),
        )
        info["projects"] = [{"name": r["proj"] or "(root)", "count": r["c"]} for r in cur.fetchall()]

        cur.execute(
            "SELECT name, node_type, last_updated FROM nodes "
            "WHERE name IS NOT NULL AND name <> '' "
            "ORDER BY last_updated DESC LIMIT 8"
        )
        info["recent"] = [
            {"name": r["name"], "type": r["node_type"], "at": r["last_updated"]}
            for r in cur.fetchall()
        ]
        conn.close()
    except Exception as exc:
        info["error"] = "db read failed: %s" % exc

    return info


def boot_nodeos():
    """Whitelisted action: start the NodeOS daemon (it self-detaches)."""
    if not NODEOS_BIN:
        return {"ok": False, "error": "nodeos binary not found"}

    result = {"ok": False}
    cwd = _workspace_cwd()
    try:
        proc = subprocess.run(
            [NODEOS_BIN, "-d"], capture_output=True, text=True, timeout=30, cwd=cwd
        )
        result.update(
            ok=proc.returncode == 0,
            code=proc.returncode,
            cwd=cwd,
            stdout=(proc.stdout or "").strip()[-2000:],
            stderr=(proc.stderr or "").strip()[-2000:],
        )
    except subprocess.TimeoutExpired:
        # -d self-detaches; a timeout usually still means the daemon came up.
        result.update(ok=True, note="boot command did not return within 30s (daemon detaches)")
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    # The daemon needs a moment to bind; re-probe with a fresh (uncached) ping.
    _ping_cache.update(online=None, at=0.0)
    for _ in range(8):
        time.sleep(1)
        if probe_daemon() is True:
            break
        _ping_cache.update(online=None, at=0.0)
    result["online"] = probe_daemon()
    return result


# --------------------------------------------------------------------------- #
# http layer
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "WorkspacePulse/1.0"
    protocol_version = "HTTP/1.1"

    # -- helpers ---------------------------------------------------------- #
    def _send(self, status, body, content_type="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _authorized(self, query):
        if self.command == "GET":
            return True
        if not TOKEN:
            return True
        supplied = self.headers.get("X-Sidecar-Token") or (query.get("token") or [""])[0]
        return supplied == TOKEN

    def log_message(self, fmt, *args):
        sys.stdout.write("[pulse] %s - %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()

    # -- routing ---------------------------------------------------------- #
    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        route = parsed.path.rstrip("/") or "/"

        user_agent = (self.headers.get("User-Agent") or "").lower()
        if route == "/term" or (route == "/" and "curl" in user_agent):
            if render_terminal:
                payload = {"ts": int(time.time()), "system": collect_system(), "nodeos": collect_nodeos()}
                term_txt = render_terminal(payload, use_color=True) + "\n"
                self._send(200, term_txt, "text/plain; charset=utf-8")
                return

        if route in ("/", VIEW_PATH):
            html = _read_text(INDEX_PATH)
            if html is None:
                self._send(500, {"error": "index.html missing"}, "application/json")
                return
            self._send(200, html, "text/html; charset=utf-8")
            return

        if route == "/health":
            self._send(200, {"status": "ok", "view": VIEW_PATH, "uptime_s": int(time.time() - STARTED)})
            return

        if route == "/api/pulse":
            payload = {"ts": int(time.time()), "system": collect_system(), "nodeos": collect_nodeos()}
            self._send(200, payload)
            return

        self._send(404, {"error": "not found", "path": parsed.path})

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        route = parsed.path.rstrip("/") or "/"

        if not self._authorized(query):
            self._send(401, {"error": "unauthorized"})
            return

        # Drain the body so keep-alive stays in sync.
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            try:
                self.rfile.read(length)
            except Exception:
                pass

        if route == "/api/nodeos/boot":
            result = boot_nodeos()
            self._send(200 if result.get("ok") else 500, result)
            return

        self._send(404, {"error": "not found", "path": parsed.path})


STARTED = time.time()


def main():
    port = int(os.environ.get("ANTIGRAVITY_SIDECAR_WEB_PORT") or 0)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    actual = httpd.server_address[1]
    sys.stdout.write("[pulse] listening on 127.0.0.1:%d view=%s\n" % (actual, VIEW_PATH))
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
