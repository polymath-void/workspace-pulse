"""Terminal rendering engine for Workspace Pulse."""

import datetime
import re
import shutil
import sys

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _visible_len(s):
    return len(ANSI_RE.sub("", s))


def _bar(pct, width=14, fill="█", empty="░"):
    if pct is None:
        return empty * width
    pct = max(0.0, min(100.0, float(pct)))
    filled_len = int(round(width * pct / 100.0))
    return (fill * filled_len) + (empty * (width - filled_len))


def _fmt_bytes(gb):
    if gb is None:
        return "n/a"
    return "%.1f GB" % gb


def _fmt_mb(mb):
    if mb is None:
        return "n/a"
    if mb >= 1024:
        return "%.1f GiB" % (mb / 1024.0)
    return "%.0f MiB" % mb


def render_terminal(data, use_color=None, width=None):
    if use_color is None:
        use_color = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    if width is None:
        term_width = shutil.get_terminal_size((80, 24)).columns
        width = max(68, min(80, term_width))

    # ANSI helpers
    if use_color:
        BOLD = "\033[1m"
        DIM = "\033[2m"
        RESET = "\033[0m"
        CYAN = "\033[36m"
        GREEN = "\033[32m"
        YELLOW = "\033[33m"
        RED = "\033[31m"
        BLUE = "\033[34m"
        MAGENTA = "\033[35m"
    else:
        BOLD = DIM = RESET = CYAN = GREEN = YELLOW = RED = BLUE = MAGENTA = ""

    sys_data = data.get("system") or {}
    nodeos = data.get("nodeos") or {}
    ts = data.get("ts")
    ts_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "live"

    inner = width - 4

    def box_border(left="┌", mid="─", right="┐"):
        return f"{CYAN}{left}{mid * (width - 2)}{right}{RESET}"

    def box_line(content=""):
        vlen = _visible_len(content)
        pad = max(0, inner - vlen)
        return f"{CYAN}│{RESET} {content}{' ' * pad} {CYAN}│{RESET}"

    lines = []
    # Header
    lines.append(box_border("┌", "─", "┐"))
    title = "⚡ WORKSPACE PULSE :: Termux Live State & NodeOS Graph"
    t_vlen = len(title)
    t_pad = max(0, inner - t_vlen)
    left_t = t_pad // 2
    right_t = t_pad - left_t
    lines.append(f"{CYAN}│{RESET} {' ' * left_t}{BOLD}{CYAN}{title}{RESET}{' ' * right_t} {CYAN}│{RESET}")
    lines.append(box_border("├", "─", "┤"))

    # SYSTEM SECTION
    lines.append(box_line(f"{BOLD}SYSTEM METRICS{RESET}"))

    # RAM
    ram_used = sys_data.get("mem_used_pct")
    ram_tot = sys_data.get("mem_total_mb")
    ram_avail = sys_data.get("mem_avail_mb")
    ram_str = "n/a"
    if ram_used is not None and ram_tot is not None and ram_avail is not None:
        ram_u_mb = ram_tot - ram_avail
        ram_bar = _bar(ram_used, 12)
        c = RED if ram_used > 85 else (YELLOW if ram_used > 70 else GREEN)
        ram_str = f"[{c}{ram_bar}{RESET}] {ram_used:4.1f}% ({_fmt_mb(ram_u_mb)} / {_fmt_mb(ram_tot)})"
    lines.append(box_line(f"  RAM        : {ram_str}"))

    # Swap
    sw_tot = sys_data.get("swap_total_mb")
    sw_free = sys_data.get("swap_free_mb")
    sw_str = "n/a"
    if sw_tot and sw_free is not None:
        sw_used_mb = sw_tot - sw_free
        sw_pct = (sw_used_mb / sw_tot) * 100.0 if sw_tot else 0.0
        sw_bar = _bar(sw_pct, 12)
        sw_str = f"[{sw_bar}] {sw_pct:4.1f}% ({_fmt_mb(sw_used_mb)} / {_fmt_mb(sw_tot)})"
    lines.append(box_line(f"  Swap       : {sw_str}"))

    # Disk
    disk_used = sys_data.get("disk_used_pct")
    disk_tot = sys_data.get("disk_total_gb")
    disk_free = sys_data.get("disk_free_gb")
    disk_str = "n/a"
    if disk_used is not None and disk_tot is not None and disk_free is not None:
        disk_u_gb = disk_tot - disk_free
        d_bar = _bar(disk_used, 12)
        dc = RED if disk_used > 90 else (YELLOW if disk_used > 75 else GREEN)
        disk_str = f"[{dc}{d_bar}{RESET}] {disk_used:4.1f}% ({_fmt_bytes(disk_u_gb)} / {_fmt_bytes(disk_tot)})"
    lines.append(box_line(f"  Disk ($HOME): {disk_str}"))

    # Battery & SoC
    batt_pct = sys_data.get("battery_pct")
    batt_temp = sys_data.get("battery_temp_c")
    batt_stat = sys_data.get("battery_status") or "BATTERY"
    cpu_m = sys_data.get("cpu_model") or "MT6886"
    cpu_c = sys_data.get("cpu_cores") or 8

    dev_parts = []
    if batt_pct is not None:
        bt_color = RED if batt_pct < 20 else (GREEN if batt_pct > 50 else YELLOW)
        temp_s = f" ({batt_temp:.1f}°C)" if batt_temp is not None else ""
        dev_parts.append(f"Batt: {bt_color}{batt_pct}%{RESET} [{batt_stat}]{temp_s}")
    dev_parts.append(f"SoC: {cpu_m} ({cpu_c} cores)")
    lines.append(box_line(f"  Device     : {' │ '.join(dev_parts)}"))

    # NODEOS AST GRAPH OR WORKSPACE SECTION
    lines.append(box_border("├", "─", "┤"))

    nodes_cnt = nodeos.get("nodes")
    edges_cnt = nodeos.get("edges")
    projects = nodeos.get("projects") or []

    if nodes_cnt is not None:
        lines.append(box_line(f"{BOLD}NODEOS AST KNOWLEDGE GRAPH{RESET}"))

        online = nodeos.get("online")
        status_tag = f"{GREEN}● ONLINE{RESET}" if online else f"{YELLOW}⚪ STANDBY (offline){RESET}"
        db_path = nodeos.get("db_path") or "none"
        db_name = db_path.split("/")[-1] if "/" in db_path else db_path
        db_age = nodeos.get("db_age_s")
        age_str = ""
        if db_age is not None:
            if db_age < 60:
                age_str = f" ({db_age}s ago)"
            elif db_age < 3600:
                age_str = f" ({db_age//60}m ago)"
            else:
                age_str = f" ({db_age//3600}h {(db_age%3600)//60}m ago)"

        lines.append(box_line(f"  Daemon     : {status_tag}  [DB: {db_name}{age_str}]"))

        n_str = f"{nodes_cnt:,}"
        e_str = f"{edges_cnt:,}" if edges_cnt is not None else "0"
        p_str = str(len(projects))
        lines.append(box_line(f"  Graph Size : {BOLD}{n_str}{RESET} Nodes  │  {BOLD}{e_str}{RESET} Edges  │  {BOLD}{p_str}{RESET} Projects"))

        # NODE TYPES BREAKDOWN
        node_types = nodeos.get("node_types") or []
        if node_types and nodes_cnt:
            lines.append(box_line())
            lines.append(box_line(f"  {DIM}Symbol Breakdown:{RESET}"))
            for item in node_types[:5]:
                t_name = item.get("type", "Unknown")
                cnt = item.get("count", 0)
                pct = (cnt / nodes_cnt) * 100.0 if nodes_cnt else 0.0
                t_bar = _bar(pct, 10, fill="■", empty=" ")
                lines.append(box_line(f"   • {t_name:<18} : {cnt:>6,}  [{CYAN}{t_bar}{RESET}] {pct:4.1f}%"))
    else:
        lines.append(box_line(f"{BOLD}WORKSPACE & REPOSITORY METRICS{RESET}"))
        ws_root = nodeos.get("workspace_root") or "workspace"
        git_b = nodeos.get("git_branch")
        if git_b:
            lines.append(box_line(f"  Git Branch : {GREEN}{git_b}{RESET}"))
        lines.append(box_line(f"  Workspace  : {ws_root}"))
        if projects:
            lines.append(box_line(f"  Projects   : {len(projects)} detected"))

    # TOP PROJECTS
    if projects:
        lines.append(box_line())
        lines.append(box_line(f"  {DIM}Top Workspace Projects:{RESET}"))
        p_items = projects[:6]
        for i in range(0, len(p_items), 2):
            p1 = p_items[i]
            c1 = f"• {p1['name'][:16]:<16} ({p1['count']:,})"
            if i + 1 < len(p_items):
                p2 = p_items[i + 1]
                c2 = f"• {p2['name'][:16]:<16} ({p2['count']:,})"
            else:
                c2 = ""
            lines.append(box_line(f"   {c1:<30} {c2}"))

    # RECENT SYMBOLS
    recent = nodeos.get("recent") or []
    if recent:
        lines.append(box_line())
        lines.append(box_line(f"  {DIM}Recently Indexed Symbols:{RESET}"))
        for sym in recent[:3]:
            s_name = sym.get("name", "")
            s_type = sym.get("type", "")
            s_at = sym.get("at", "")
            time_part = s_at.split()[-1] if " " in s_at else s_at
            lines.append(box_line(f"   › {MAGENTA}{s_type:<12}{RESET} {BOLD}{s_name[:22]:<22}{RESET} {DIM}({time_part}){RESET}"))

    # Footer
    lines.append(box_border("├", "─", "┤"))
    foot = f"Time: {ts_str}  │  pulse --watch  │  pulse --web  │  pulse --boot"
    lines.append(box_line(f"{DIM}{foot}{RESET}"))
    lines.append(box_border("└", "─", "┘"))

    return "\n".join(lines)
