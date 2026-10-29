"""A notification's scan-friendly layout and its Telegram HTML rendering.

`Layout` is the structured twin of a notification's plain body: the same already-checked strings,
arranged as blocks (a bold heading, lines under it, optionally folded into an expandable quote).
Nothing here parses text. The builder that writes the plain body also writes the layout, and every
string reaches the HTML through `html.escape`, so the markup is limited to `<b>`, `<i>` and
`<blockquote expandable>` and can never be broken by a stock name containing `&`, `<` or `>`.

Trimming works on the blocks and renders afterwards, so a cut can never land inside a tag or an
entity, and the footer is rendered last whatever is dropped. LINE gets `render_text`: the same
blocks, blank lines between them, no markup.
"""

from __future__ import annotations

import html
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

TELEGRAM_MAX_UNITS = 4096
LINE_MAX_UNITS = 5000
LINE_FOLD_LINES = 3
LINE_FOLD_MORE = "…（其餘請見 Miru）"
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


def _fit(
    layout: Layout, footer: str, *, pointer: str, limit: int, render: Callable[[Layout, str], str]
) -> str | None:
    blocks = list(layout.blocks)
    trimmed = False
    while True:
        shown = (*blocks, LayoutBlock(lines=(pointer,))) if trimmed else tuple(blocks)
        text = render(Layout(blocks=shown), footer)
        if utf16_units(text) <= limit:
            return text
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


def fit_html(
    layout: Layout, footer: str, *, pointer: str, limit: int = TELEGRAM_MAX_UNITS
) -> str | None:
    """The HTML within `limit` UTF-16 units (tags and entities counted, so it is conservative), or
    None when even the first block alone does not fit. Lines are dropped from the end of the last
    block, then empty blocks, with `pointer` marking the cut; the first block and footer stay."""
    return _fit(layout, footer, pointer=pointer, limit=limit, render=render_html)


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
    "TELEGRAM_MAX_UNITS",
    "Layout",
    "LayoutBlock",
    "fit_html",
    "fit_text",
    "line_view",
    "render_html",
    "render_text",
    "utf16_units",
]
