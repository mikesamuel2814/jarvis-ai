"""
Jarvis v3 Telegram Dynamic UI Templates
8 message component types.
"""

from typing import Dict, List, Optional


# ── Status Card ─────────────────────────────────────────────────────────
def status_card(cpu: float, ram: float, disk: float, gpu: Optional[float] = None) -> str:
    cpu_emoji = "🟢" if cpu < 50 else "🟡" if cpu < 80 else "🔴"
    ram_emoji = "🟢" if ram < 50 else "🟡" if ram < 80 else "🔴"
    disk_emoji = "🟢" if disk < 50 else "🟡" if disk < 80 else "🔴"
    lines = [
        "🖥️ *System Status*",
        f"CPU: {cpu}% {cpu_emoji}",
        f"RAM: {ram}% {ram_emoji}",
        f"Disk: {disk}% {disk_emoji}",
    ]
    if gpu is not None:
        gpu_emoji = "🟢" if gpu < 50 else "🟡" if gpu < 80 else "🔴"
        lines.append(f"GPU VRAM: {gpu}% {gpu_emoji}")
    return "\n".join(lines)


# ── Progress Bar ────────────────────────────────────────────────────────
def progress_bar(percent: float, width: int = 20) -> str:
    filled = int(width * percent / 100)
    bar = "█" * filled + "░" * (width - filled)
    return f"`[{bar}] {percent:.0f}%`"


# ── Code Block ──────────────────────────────────────────────────────────
def code_block(code: str, language: str = "bash") -> str:
    return f"```{language}\n{code[:3000]}\n```"


# ── Inline Buttons ──────────────────────────────────────────────────────
def inline_buttons(buttons: List[Dict[str, str]]) -> Dict:
    """Returns InlineKeyboardMarkup structure."""
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    rows = []
    for btn in buttons:
        rows.append([InlineKeyboardButton(btn["text"], callback_data=btn["callback_data"])])
    return {"reply_markup": InlineKeyboardMarkup(rows)}


# ── Data Table ──────────────────────────────────────────────────────────
def data_table(headers: List[str], rows: List[List[str]]) -> str:
    # Monospace column formatting
    col_widths = [max(len(str(h)), max((len(str(r[i])) for r in rows), default=0)) + 2 for i, h in enumerate(headers)]
    def fmt_row(cells):
        return " | ".join(str(c).ljust(col_widths[i]) for i, c in enumerate(cells))
    lines = ["`" + fmt_row(headers) + "`", "`" + "-" * sum(col_widths) + "`"]
    for row in rows:
        lines.append("`" + fmt_row(row) + "`")
    return "\n".join(lines)


# ── Alert Banner ────────────────────────────────────────────────────────
def alert_banner(severity: str, title: str, message: str, actions: Optional[List[str]] = None) -> str:
    emoji = {"P0": "🚨", "P1": "⚠️", "P2": "🔶", "P3": "🔹", "P4": "ℹ️", "P5": "🛡️", "P6": "📈"}.get(severity, "⚪")
    lines = [f"{emoji} *{title}*", f"Severity: {severity}", f"{message}"]
    if actions:
        lines.append(f"Actions: {', '.join(actions)}")
    return "\n".join(lines)


# ── Expandable Section ──────────────────────────────────────────────────
def expandable_section(title: str, content: str) -> str:
    # Telegram blockquote for expandable feel
    lines = [f"*{title}*", f"> {content[:500].replace(chr(10), chr(10) + '> ')}"]
    return "\n".join(lines)


# ── Permission Request ──────────────────────────────────────────────────
def permission_request(
    command: str,
    rank: str,
    downtime_sec: Optional[int] = None,
    rollback: Optional[str] = None,
    system_context: Optional[Dict] = None,
    project_impact: Optional[Dict] = None,
) -> str:
    emoji = {"R0": "🔵", "R1": "🟢", "R2": "🟡", "R3": "🟠", "R4": "🔴", "R5": "🟣", "R6": "⚫"}.get(rank, "⚪")
    lines = [
        "Jarvis needs permission:",
        "",
        f"`{command}`",
        "",
        f"Rank: {rank} [{emoji}]",
    ]
    if downtime_sec is not None:
        lines.append(f"Downtime: ~{downtime_sec}s")
    if rollback:
        lines.append(f"Rollback: `{rollback}`")
    lines.append("")
    if system_context:
        lines.append(
            f"System: CPU {system_context.get('cpu', '?')}% | "
            f"RAM {system_context.get('ram', '?')}% | "
            f"Disk {system_context.get('disk', '?')}%"
        )
    if project_impact:
        for proj, status in project_impact.items():
            lines.append(f"{proj}: {status}")
    return "\n".join(lines)
