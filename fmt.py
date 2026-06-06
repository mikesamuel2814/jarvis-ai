import re


def esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def bold(text: str) -> str:
    return f"<b>{text}</b>"


def italic(text: str) -> str:
    return f"<i>{text}</i>"


def code(text: str) -> str:
    return f"<code>{esc(text)}</code>"


def pre(text: str, lang: str = "") -> str:
    if lang:
        return f'<pre><code class="language-{lang}">{esc(text)}</code></pre>'
    return f"<pre><code>{esc(text)}</code></pre>"


def link(label: str, url: str) -> str:
    return f'<a href="{url}">{label}</a>'


def mono(text: str) -> str:
    return code(text)


def section(title: str, body: str) -> str:
    return f"<b>── {title.upper()} ──</b>\n{body}"


def card(title: str, url: str, body: str, meta: str = "") -> str:
    header = bold(link(title, url))
    lines = [header]
    if meta:
        lines.append(italic(meta))
    snippet = esc(body[:280])
    lines.append(snippet)
    return "\n".join(lines)


def status_line(label: str, value, ok: bool | None = None) -> str:
    icon = {True: "✅", False: "❌", None: "ℹ️"}[ok]
    return f"{icon} <b>{label}:</b> <code>{esc(str(value))}</code>"


def progress_bar(pct: float, width: int = 10) -> str:
    filled = round(max(0.0, min(1.0, pct / 100)) * width)
    return "█" * filled + "░" * (width - filled)


def split_smart(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        chunk = text[:limit]
        split_at = chunk.rfind("\n\n")
        if split_at == -1:
            split_at = chunk.rfind("\n")
        if split_at == -1:
            split_at = chunk.rfind(" ")
        if split_at <= 0:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def strip_markdown(text: str) -> str:
    fenced_re = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)

    segments: list[tuple[str, bool]] = []
    last = 0
    for m in fenced_re.finditer(text):
        if m.start() > last:
            segments.append((text[last:m.start()], False))
        segments.append((m.group(0), True))
        last = m.end()
    if last < len(text):
        segments.append((text[last:], False))

    result_parts: list[str] = []
    for segment, is_code_block in segments:
        if is_code_block:
            m = fenced_re.match(segment)
            lang = m.group(1)
            code_body = m.group(2)
            if lang:
                result_parts.append(f'<pre><code class="language-{lang}">{esc(code_body)}</code></pre>')
            else:
                result_parts.append(f"<pre><code>{esc(code_body)}</code></pre>")
            continue

        inline_re = re.compile(
            r"(\*\*(.+?)\*\*)"
            r"|(\*(?!\*)(.+?)(?<!\*)\*(?!\*))"
            r"|(`(.+?)`)"
            r"|(\[(.+?)\]\((.+?)\))",
            re.DOTALL,
        )

        plain_parts: list[tuple[int, int, str]] = []
        last_pos = 0
        for m in inline_re.finditer(segment):
            if m.start() > last_pos:
                plain_parts.append((last_pos, m.start(), "plain"))
            plain_parts.append((m.start(), m.end(), "match"))
            last_pos = m.end()
        if last_pos < len(segment):
            plain_parts.append((last_pos, len(segment), "plain"))

        out: list[str] = []
        for start, end, kind in plain_parts:
            chunk = segment[start:end]
            if kind == "plain":
                out.append(esc(chunk))
            else:
                m2 = inline_re.match(chunk)
                if m2.group(1):
                    out.append(bold(m2.group(2)))
                elif m2.group(3):
                    out.append(italic(m2.group(4)))
                elif m2.group(5):
                    out.append(code(m2.group(6)))
                elif m2.group(7):
                    out.append(link(m2.group(8), m2.group(9)))
        result_parts.append("".join(out))

    return "".join(result_parts)
