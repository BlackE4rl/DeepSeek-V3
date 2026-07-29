"""HTML-Erzeugung: schlanker Markdown-Renderer und Seitenlayout.

Bewusst ohne Fremdbibliotheken und ohne Durchreichen von Roh-HTML: Der Text der
Verteilung wird immer maskiert, bevor die erlaubten Auszeichnungen gesetzt werden.
Die Seiten laden keine externen Ressourcen (keine Schriften, keine Zählpixel).
"""

from __future__ import annotations

import html
import re

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_SAFE_SCHEMES = ("https://", "http://", "mailto:", "otpauth://")


def escape(value: str) -> str:
    return html.escape(value or "", quote=True)


def _inline(text: str) -> str:
    out = escape(text)
    out = _INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)
    out = _ITALIC.sub(lambda m: f"<em>{m.group(1)}</em>", out)

    def link(match: re.Match[str]) -> str:
        label, target = match.group(1), html.unescape(match.group(2))
        if not target.startswith(_SAFE_SCHEMES):
            return label  # relative oder unbekannte Schemata werden entwertet
        return f'<a href="{escape(target)}" rel="noopener noreferrer">{label}</a>'

    return _LINK.sub(link, out)


def markdown_to_html(text: str) -> str:
    """Unterstützt Überschriften, Absätze, Listen, Tabellen, Zitate und Trennlinien."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        if re.fullmatch(r"-{3,}|_{3,}|\*{3,}", stripped):
            out.append("<hr>")
            index += 1
            continue

        heading = re.match(r"(#{1,6})\s+(.*)", stripped)
        if heading:
            level = min(len(heading.group(1)) + 1, 6)  # h1 bleibt dem Seitentitel vorbehalten
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            block: list[str] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                block.append(lines[index].strip())
                index += 1
            out.append(_table(block))
            continue

        if stripped.startswith(">"):
            block = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                block.append(lines[index].strip().lstrip(">").strip())
                index += 1
            out.append(f"<blockquote>{_inline(' '.join(block))}</blockquote>")
            continue

        if re.match(r"[-*+]\s+", stripped):
            items = []
            while index < len(lines) and re.match(r"\s*[-*+]\s+", lines[index]):
                items.append(_inline(re.sub(r"\s*[-*+]\s+", "", lines[index], count=1)))
                index += 1
            out.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
            continue

        if re.match(r"\d+[.)]\s+", stripped):
            items = []
            while index < len(lines) and re.match(r"\s*\d+[.)]\s+", lines[index]):
                items.append(_inline(re.sub(r"\s*\d+[.)]\s+", "", lines[index], count=1)))
                index += 1
            out.append("<ol>" + "".join(f"<li>{item}</li>" for item in items) + "</ol>")
            continue

        paragraph = []
        while index < len(lines) and lines[index].strip() and not _breaks_paragraph(lines[index]):
            paragraph.append(lines[index].strip())
            index += 1
        out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
    return "\n".join(out)


def _breaks_paragraph(line: str) -> bool:
    stripped = line.strip()
    return bool(
        stripped.startswith(("#", "|", ">"))
        or re.match(r"[-*+]\s+", stripped)
        or re.match(r"\d+[.)]\s+", stripped)
        or re.fullmatch(r"-{3,}|_{3,}|\*{3,}", stripped)
    )


def _table(block: list[str]) -> str:
    def cells(row: str) -> list[str]:
        return [cell.strip() for cell in row.strip().strip("|").split("|")]

    if len(block) >= 2 and re.fullmatch(r"[\s|:-]+", block[1]):
        header, body = cells(block[0]), block[2:]
    else:
        header, body = [], block
    parts = ['<div class="tablewrap"><table>']
    if header:
        parts.append(
            "<thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in header) + "</tr></thead>"
        )
    parts.append("<tbody>")
    for row in body:
        parts.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells(row)) + "</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


CSS = """
:root { color-scheme: light dark; --fg:#1a1a1a; --muted:#5c5c5c; --bg:#ffffff;
        --card:#f7f7f8; --line:#d9d9de; --accent:#1f5fa9; --ok:#1a7f37; --warn:#a15c00;
        --err:#b3261e; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e8e8ea; --muted:#a5a5ad; --bg:#16171a; --card:#212227; --line:#3a3b42;
          --accent:#7fb0ea; --ok:#5dbb75; --warn:#e0a458; --err:#f2867c; }
}
* { box-sizing: border-box; }
body { margin:0; padding:2rem 1rem 4rem; background:var(--bg); color:var(--fg);
       font: 16px/1.6 -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
main { max-width: 46rem; margin: 0 auto; }
header.brand { color:var(--muted); font-size:.85rem; letter-spacing:.06em;
       text-transform:uppercase; margin-bottom:1rem; }
h1 { font-size:1.6rem; line-height:1.3; margin:0 0 .5rem; }
h2 { font-size:1.2rem; margin:1.8rem 0 .4rem; }
h3 { font-size:1.05rem; margin:1.4rem 0 .3rem; }
.meta { color:var(--muted); font-size:.9rem; margin-bottom:1.5rem; }
.content { border:1px solid var(--line); border-radius:10px; padding:1.25rem 1.5rem;
       background:var(--card); }
.content > :first-child { margin-top:0; }
.tablewrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; margin:.6rem 0; font-size:.94rem; }
th, td { border:1px solid var(--line); padding:.4rem .6rem; text-align:left; vertical-align:top; }
th { background:rgba(127,127,127,.12); }
code { background:rgba(127,127,127,.16); padding:.1rem .3rem; border-radius:4px;
       font-size:.9em; word-break:break-all; }
blockquote { margin:.8rem 0; padding:.5rem .9rem; border-left:3px solid var(--accent);
       color:var(--muted); }
a { color:var(--accent); }
form { margin-top:1.5rem; border:1px solid var(--line); border-radius:10px; padding:1.25rem 1.5rem; }
label { display:block; font-weight:600; margin-bottom:.35rem; }
input[type=text] { font-size:1.4rem; letter-spacing:.35em; padding:.5rem .6rem; width:9.5em;
       border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); }
button { margin-top:1rem; font-size:1rem; font-weight:600; padding:.7rem 1.4rem; border:0;
       border-radius:6px; background:var(--accent); color:#fff; cursor:pointer; }
button:hover { filter:brightness(1.08); }
.notice { border-radius:8px; padding:.8rem 1rem; margin:1rem 0; border:1px solid var(--line); }
.notice.ok  { border-color:var(--ok);  color:var(--ok); }
.notice.warn{ border-color:var(--warn);color:var(--warn); }
.notice.err { border-color:var(--err); color:var(--err); }
.secret { font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
       font-size:1.1rem; letter-spacing:.12em; background:rgba(127,127,127,.16);
       padding:.5rem .7rem; border-radius:6px; display:inline-block; word-break:break-all; }
footer { color:var(--muted); font-size:.85rem; margin-top:2.5rem; border-top:1px solid var(--line);
       padding-top:1rem; }
.statement { font-style:italic; }
"""


def page(title: str, body: str, *, organisation: str = "", footer: str = "") -> bytes:
    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<main>
<header class="brand">{escape(organisation)}</header>
{body}
<footer>{footer}</footer>
</main>
</body>
</html>""".encode(
        "utf-8"
    )
