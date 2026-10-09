"""A small Markdown subset renderer for the site's prose pages.

Supports headings, paragraphs, bullet and numbered lists, pipe tables, fenced code blocks,
inline code, links, bold and italics. Input is maintainer-written documentation.
"""

from __future__ import annotations

import html
import re
from typing import Callable

_INLINE_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*([^*\s][^*]*)\*(?![\w*])")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def inline(text: str, link: Callable[[str], str] = lambda u: u) -> str:
    codes: list[str] = []

    def stash(match: re.Match) -> str:
        codes.append(f"<code>{html.escape(match.group(1))}</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = _INLINE_CODE.sub(stash, text)
    text = html.escape(text, quote=False)
    text = _LINK.sub(lambda m: f'<a href="{html.escape(link(m.group(2)))}">{m.group(1)}</a>', text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)


def render(source: str, *, link: Callable[[str], str] = lambda u: u, shift: int = 0) -> str:
    """Render Markdown to HTML; ``shift`` demotes headings (1 makes '#' an h2)."""
    lines = source.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            cls = f' class="language-{html.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(body))}</code></pre>")
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = min(6, len(heading.group(1)) + shift)
            text = heading.group(2).strip()
            out.append(f'<h{level} id="{_slug(text)}">{inline(text, link)}</h{level}>')
            i += 1
            continue
        if stripped.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            header, body_rows = rows[0], [r for r in rows[1:] if not all(
                re.fullmatch(r":?-{3,}:?", c) for c in r)]
            head = "".join(f"<th>{inline(c, link)}</th>" for c in header)
            body = "".join(
                "<tr>" + "".join(f"<td>{inline(c, link)}</td>" for c in r) + "</tr>"
                for r in body_rows
            )
            out.append(f'<div class="table-scroll"><table><thead><tr>{head}</tr></thead>'
                       f"<tbody>{body}</tbody></table></div>")
            continue
        if stripped.startswith(">"):
            quoted = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quoted.append(lines[i].strip()[1:].strip())
                i += 1
            out.append(f"<blockquote><p>{inline(' '.join(quoted), link)}</p></blockquote>")
            continue
        bullet = re.match(r"^([-*]|\d+\.)\s+", stripped)
        if bullet:
            ordered = stripped[0].isdigit()
            items: list[str] = []
            while i < len(lines):
                current = lines[i].strip()
                match = re.match(r"^([-*]|\d+\.)\s+(.*)$", current)
                if match:
                    items.append(match.group(2))
                elif current and lines[i].startswith((" ", "\t")) and items:
                    items[-1] += " " + current
                else:
                    break
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(t, link)}</li>" for t in items)
                       + f"</{tag}>")
            continue
        para = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^(#{1,6}\s|```|\||>|[-*]\s|\d+\.\s)", lines[i].strip()
        ):
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{inline(' '.join(para), link)}</p>")
    return "\n".join(out)
