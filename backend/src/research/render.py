"""The finished report as markdown for the chat bubble: [n] citations and a source list."""

from __future__ import annotations

from typing import Literal

from governance.textscan import check_output
from research.models import FinalReport, ReportClaim, StopReason

Locale = Literal["zh-TW", "en"]

_LABELS: dict[Locale, dict[str, str]] = {
    "zh-TW": {
        "as_of": "資料截至",
        "summary": "重點摘要",
        "open": "尚未釐清",
        "sources": "來源",
        "scope": "本次研究讀取了 {pages} 個網頁、進行 {searches} 次搜尋。",
        "empty": "這次沒有找到足夠可引用的公開資料，無法產出研究報告。請換個問法或縮小範圍再試。",
        "failed": "研究過程發生錯誤，未產出報告。請稍後再試。",
    },
    "en": {
        "as_of": "As of",
        "summary": "Key points",
        "open": "Still unresolved",
        "sources": "Sources",
        "scope": "Read {pages} web pages across {searches} searches.",
        "empty": "No sufficiently citable public sources were found, so no report was produced. "
        "Try rephrasing or narrowing the question.",
        "failed": "The research hit an error and produced no report. Please try again later.",
    },
}

_EARLY_STOP: dict[Locale, dict[StopReason, str]] = {
    "zh-TW": {
        "rounds": "已達研究輪數上限，以下為目前已讀資料的整理。",
        "searches": "已達搜尋次數上限，以下為目前已讀資料的整理。",
        "pages": "已達閱讀頁數上限，以下為目前已讀資料的整理。",
        "time": "已達時間上限，以下為目前已讀資料的整理。",
    },
    "en": {
        "rounds": "Stopped at the research-round limit; this covers what was read so far.",
        "searches": "Stopped at the search limit; this covers what was read so far.",
        "pages": "Stopped at the page-reading limit; this covers what was read so far.",
        "time": "Stopped at the time limit; this covers what was read so far.",
    },
}


_MARKDOWN_ACTIVE = str.maketrans({c: "\\" + c for c in "\\[]()<>!`"})


_TITLE_MAX_CHARS = 120


def _inline(text: str) -> str:
    """Untrusted text as one line: a newline would end the bullet or link it sits in (a source
    title can be a whole multi-paragraph post), and a `- ` after one would start a new bullet."""
    return " ".join(text.split())


def _plain(text: str) -> str:
    """Model/page-derived text shown as plain text: no links, images or HTML can be formed."""
    return _inline(text).translate(_MARKDOWN_ACTIVE)


def _short(text: str) -> str:
    one_line = _inline(text)
    if len(one_line) <= _TITLE_MAX_CHARS:
        return one_line
    return one_line[: _TITLE_MAX_CHARS - 1].rstrip() + "…"


def _line(claim: ReportClaim) -> str:
    cites = "".join(f"[{n}]" for n in claim.source_ids)
    return f"- {_plain(claim.text)} {cites}".rstrip()


def _scan_title(title: str) -> str:
    """Page titles are untrusted prose: they get the same trading-language scan as claims."""
    verdict = check_output(title, has_market_data=False)
    return verdict.redacted_text if verdict.redacted_text is not None else title


def failure_text(locale: Locale) -> str:
    return _LABELS[locale]["failed"]


def render_markdown(report: FinalReport, locale: Locale) -> str:
    labels = _LABELS[locale]
    if not report.has_content:
        return labels["empty"]
    parts = [f"## {_plain(report.title)}", f"*{labels['as_of']} {report.as_of.isoformat()}*"]
    note = _EARLY_STOP[locale].get(report.stopped_because)
    if note:
        parts.append(f"> {note}")
    if report.summary:
        parts.append(f"### {labels['summary']}\n" + "\n".join(_line(c) for c in report.summary))
    for section in report.sections:
        parts.append(
            f"### {_plain(section.heading)}\n" + "\n".join(_line(c) for c in section.claims)
        )
    if report.open_questions:
        parts.append(
            f"### {labels['open']}\n" + "\n".join(f"- {_plain(q)}" for q in report.open_questions)
        )
    markdown = "\n\n".join(parts)
    # Backstop: the prose is scanned once more and, as it draws on external market information,
    # carries the standard non-trading caveat.
    verdict = check_output(markdown, has_market_data=True)
    body = verdict.redacted_text if verdict.redacted_text is not None else markdown
    # URLs are validated data, not prose: the scanner's vendor-name stripping would rewrite a
    # "yahoo" inside one and break the link. Titles, which are prose, are scanned one by one.
    sources = f"### {labels['sources']}\n" + "\n".join(
        f"{s.id}. [{_plain(_short(_scan_title(s.title)))}]({s.url})" for s in report.sources
    )
    scope = f"*{labels['scope'].format(pages=report.pages_read, searches=report.searches)}*"
    return "\n\n".join([body, sources, scope])


__all__ = ["Locale", "failure_text", "render_markdown"]
