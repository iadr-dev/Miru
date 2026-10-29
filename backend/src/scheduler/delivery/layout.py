"""A notification's scan-friendly layout and its Telegram HTML rendering.

`Layout` is the structured twin of a notification's plain body: the same already-checked strings,
arranged as blocks (a bold heading, lines under it, optionally folded into an expandable quote).
Nothing here parses text. The builder that writes the plain body also writes the layout, and every
string reaches the HTML through `html.escape`, so the markup is limited to `<b>`, `<i>` and
`<blockquote expandable>` and can never be broken by a stock name containing `&`, `<` or `>`.

Trimming works on the blocks and renders afterwards, so a cut can never land inside a tag or an
entity, and the footer is rendered last whatever is dropped. LINE gets `render_text`: the same
blocks, blank lines between them, no markup. Email gets `render_email_html`: the same blocks as
headings and one paragraph per line, a folded block shown like any other. The in-app inbox stores
a layout trimmed by `trim_layout` and the web app draws it.
"""

from __future__ import annotations

import html
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

TELEGRAM_MAX_UNITS = 4096
LINE_MAX_UNITS = 5000
LINE_FOLD_LINES = 3
LINE_FOLD_MORE = "…（其餘請見 Miru）"
TRIM_POINTER = "…（完整內容請見 Miru）"
STORED_MAX_UNITS = LINE_MAX_UNITS
_BLOCK_GAP = "\n\n"


class LayoutBlock(BaseModel):
    """`head` renders bold; each of `lines` is one line under it; a `fold` block is one expandable
    quote, collapsed by Telegram past a few lines."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    head: str = ""
    lines: tuple[str, ...] = ()
    fold: bool = False


class Layout(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    blocks: tuple[LayoutBlock, ...]


def utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _block_html(block: LayoutBlock) -> str:
    rows = [f"<b>{_escape(block.head)}</b>"] if block.head else []
    rows.extend(_escape(line) for line in block.lines)
    inner = "\n".join(rows)
    return f"<blockquote expandable>{inner}</blockquote>" if block.fold else inner


def render_html(layout: Layout, footer: str) -> str:
    parts = [_block_html(b) for b in layout.blocks if b.head or b.lines]
    parts.append(f"<i>{_escape(footer)}</i>")
    return _BLOCK_GAP.join(parts)


def render_text(layout: Layout, footer: str) -> str:
    """The layout as plain text, markup-free: what the HTML says once its tags are removed."""
    parts = ["\n".join([b.head, *b.lines] if b.head else b.lines) for b in layout.blocks]
    return _BLOCK_GAP.join([*(p for p in parts if p), footer])


def _trim(
    layout: Layout, footer: str, *, pointer: str, limit: int, render: Callable[[Layout, str], str]
) -> Layout | None:
    blocks = list(layout.blocks)
    trimmed = False
    while True:
        shown = Layout(
            blocks=(*blocks, LayoutBlock(lines=(pointer,))) if trimmed else tuple(blocks)
        )
        if utf16_units(render(shown, footer)) <= limit:
            return shown
        if not blocks:
            return None
        last = blocks[-1]
        if last.lines:
            blocks[-1] = last.model_copy(update={"lines": last.lines[:-1]})
        elif len(blocks) > 1:
            blocks.pop()
        else:
            return None
        trimmed = True


def _fit(
    layout: Layout, footer: str, *, pointer: str, limit: int, render: Callable[[Layout, str], str]
) -> str | None:
    shown = _trim(layout, footer, pointer=pointer, limit=limit, render=render)
    return None if shown is None else render(shown, footer)


def trim_layout(
    layout: Layout, footer: str, *, pointer: str = TRIM_POINTER, limit: int = STORED_MAX_UNITS
) -> Layout | None:
    """The layout cut, the way `fit_html` cuts, until its plain text fits `limit` UTF-16 units, or
    None when even the first block alone does not. This is the copy the inbox and email keep."""
    return _trim(layout, footer, pointer=pointer, limit=limit, render=render_text)


def fit_html(
    layout: Layout, footer: str, *, pointer: str, limit: int = TELEGRAM_MAX_UNITS
) -> str | None:
    """The HTML within `limit` UTF-16 units (tags and entities counted, so it is conservative), or
    None when even the first block alone does not fit. Lines are dropped from the end of the last
    block, then empty blocks, with `pointer` marking the cut; the first block and footer stay."""
    return _fit(layout, footer, pointer=pointer, limit=limit, render=render_html)


_EMAIL_HEAD_STYLE = "margin:0 0 6px;font-size:{size}px;line-height:1.4"
_EMAIL_LINE_STYLE = "margin:0 0 4px;font-size:15px;line-height:1.6"
_EMAIL_BLOCK_STYLE = "margin:0 0 18px"
_EMAIL_FOOTER_STYLE = "margin:0;font-size:12px;line-height:1.5;color:#555555"


def _email_block(block: LayoutBlock, *, lead: bool) -> str:
    rows: list[str] = []
    if block.head:
        tag, size = ("h1", 20) if lead else ("h2", 17)
        style = _EMAIL_HEAD_STYLE.format(size=size)
        rows.append(f'<{tag} style="{style}">{html.escape(block.head)}</{tag}>')
    rows.extend(
        f'<p style="{_EMAIL_LINE_STYLE}">{html.escape(line)}</p>' for line in block.lines if line
    )
    return f'<div style="{_EMAIL_BLOCK_STYLE}">{"".join(rows)}</div>'


def render_email_html(layout: Layout, footer: str) -> str:
    """The layout as the inside of an email body: the first heading is the page's `h1`, later ones
    `h2`, each line its own paragraph, the footer last. A folded block reads like any other (a mail
    client cannot expand it), and every string is escaped."""
    shown = [b for b in layout.blocks if b.head or b.lines]
    parts = [_email_block(b, lead=i == 0) for i, b in enumerate(shown)]
    parts.append(f'<p style="{_EMAIL_FOOTER_STYLE}">{html.escape(footer)}</p>')
    return "".join(parts)


def line_view(layout: Layout) -> Layout:
    """The layout as LINE shows it. LINE text has no expandable quote, so a folded block becomes a
    normal one, cut to `LINE_FOLD_LINES` lines (marked with `LINE_FOLD_MORE`) to stay short."""
    blocks: list[LayoutBlock] = []
    for block in layout.blocks:
        if not block.fold:
            blocks.append(block)
            continue
        cut = len(block.lines) > LINE_FOLD_LINES
        lines = (*block.lines[:LINE_FOLD_LINES], *((LINE_FOLD_MORE,) if cut else ()))
        blocks.append(LayoutBlock(head=block.head, lines=lines))
    return Layout(blocks=tuple(blocks))


def fit_text(
    layout: Layout, footer: str, *, pointer: str, limit: int = LINE_MAX_UNITS
) -> str | None:
    """The LINE text within `limit` UTF-16 units, trimmed the way `fit_html` trims."""
    return _fit(line_view(layout), footer, pointer=pointer, limit=limit, render=render_text)


__all__ = [
    "LINE_FOLD_LINES",
    "LINE_FOLD_MORE",
    "LINE_MAX_UNITS",
    "STORED_MAX_UNITS",
    "TELEGRAM_MAX_UNITS",
    "TRIM_POINTER",
    "Layout",
    "LayoutBlock",
    "fit_html",
    "fit_text",
    "line_view",
    "render_email_html",
    "render_html",
    "render_text",
    "trim_layout",
    "utf16_units",
]
