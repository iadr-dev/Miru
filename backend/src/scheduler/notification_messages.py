"""What a rule-alert, scheduled-report, morning-brief or digest notification looks like on a phone.

Each builder writes the `Layout` that sits next to the plain body the in-app inbox and email show,
from the same facts, so the two cannot drift. Pure functions, no I/O. Copy is fact phrasing only;
the worker scans every layout's text before it is sent.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date

from governance.textscan import check_output
from scheduler.delivery.layout import Layout, LayoutBlock
from scheduler.evaluator import Evaluation
from scheduler.strategy_messages import safe_message
from schemas.alerts import NON_ADVICE_FOOTER

_OPEN_REPORT = "完整報告請見 Miru"


def alert_layout(evaluation: Evaluation, as_of: date, *, quoted: bool = True) -> Layout:
    """A fired rule: the stock once as the heading, the observed fact, the rule, the data date.
    `quoted=False` leaves out third-party text (see `Evaluation.plain_fact`)."""
    fact = evaluation.fact if quoted or evaluation.plain_fact is None else evaluation.plain_fact
    return Layout(
        blocks=(
            LayoutBlock(
                head=f"🔔 {evaluation.subject}",
                lines=(
                    fact,
                    f"條件：{evaluation.condition_text}",
                    f"資料日期：{as_of.isoformat()}",
                ),
            ),
        )
    )


def safe_alert_message(evaluation: Evaluation, as_of: date) -> tuple[str, Layout | None]:
    """The body (with the non-advice footer) and layout of a fired rule that pass the output scan:
    the quoted version, else the version without third-party text, else a fixed line."""
    candidates = [(evaluation.message, alert_layout(evaluation, as_of))]
    if evaluation.plain_message is not None:
        candidates.append((evaluation.plain_message, alert_layout(evaluation, as_of, quoted=False)))
    return safe_message(
        candidates, f"{evaluation.symbol} 的條件已達成：{evaluation.condition_text}"
    )


_REPORT_PREVIEW_CHARS = 160


def report_preview(text: str) -> str:
    """A scheduled report can be thousands of characters; the notification is a pointer to the
    thread (its deep link), so it carries only the opening of the report, cut at a line break when
    one is near. The full text, tables and charts live in the schedule's thread."""
    flat = " ".join(line.strip() for line in text.strip().splitlines() if line.strip())
    if len(flat) <= _REPORT_PREVIEW_CHARS:
        return flat
    return f"{flat[:_REPORT_PREVIEW_CHARS].rstrip()}…（完整報告請見對話）"


_REPORT_HEADING = "📰 排程摘要"
_REPORT_BULLETS = 3
_BULLET_CHARS = 80
_RULE_LINE = re.compile(r"[-*_=:|\s]{3,}")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_TAG = re.compile(r"<[^>]+>")
_BLOCK_MARK = re.compile(r"^(?:#{1,6}\s*|>\s*|[-*+]\s+|\d+[.)]\s+)+")
_STARS = re.compile(r"(\*{1,3}|~~)(.+?)\1")
_UNDERSCORES = re.compile(r"(?<![A-Za-z0-9])(_{1,2})(.+?)\1(?![A-Za-z0-9])")
_HAS_TEXT = re.compile(r"\w")
_SENTENCE_END = re.compile(r"[。！？；]|[.!?;](?=\s|$)")
_CLAUSE_END = re.compile(r"[，、：]|[,:](?=\s)")


def plain_lines(markdown: str) -> list[str]:
    """The readable lines of a markdown report: no heading marks, emphasis, list markers, links,
    tags or code, and none of its table rows or horizontal rules."""
    lines: list[str] = []
    in_code = False
    for raw in markdown.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line or line.startswith("|") or _RULE_LINE.fullmatch(line):
            continue
        line = _IMAGE.sub("", line)
        line = _LINK.sub(r"\1", line)
        line = _TAG.sub("", _URL.sub("", line))
        line = _BLOCK_MARK.sub("", line).replace("`", "")
        line = _UNDERSCORES.sub(r"\2", _STARS.sub(r"\2", line))
        line = " ".join(line.split())
        if _HAS_TEXT.search(line):
            lines.append(line)
    return lines


def _short(line: str) -> str | None:
    """`line` if it is short, else its first sentence or last clause that fits, else None. A cut
    falls on punctuation or a space, never inside a word."""
    if len(line) <= _BULLET_CHARS:
        return line
    window = line[: _BULLET_CHARS + 1]
    sentence = [m.end() for m in _SENTENCE_END.finditer(window) if m.end() <= _BULLET_CHARS]
    if sentence:
        return line[: sentence[0]]
    clause = [m.start() for m in _CLAUSE_END.finditer(window) if m.start() <= _BULLET_CHARS]
    if clause and clause[-1] > 0:
        return f"{line[: clause[-1]]}…"
    space = window.rfind(" ")
    return f"{line[:space].rstrip()}…" if space > 0 else None


def report_layout(report: str) -> Layout:
    """A scheduled report as a heading, up to three short lines from its opening, and a pointer to
    the full report in Miru."""
    bullets: list[str] = []
    for line in plain_lines(report):
        if len(bullets) == _REPORT_BULLETS:
            break
        if line.endswith(("：", ":")):
            continue
        if (short := _short(line)) is not None:
            bullets.append(f"• {short}")
    return Layout(
        blocks=(
            LayoutBlock(head=_REPORT_HEADING, lines=tuple(bullets)),
            LayoutBlock(lines=(_OPEN_REPORT,)),
        )
    )


_BRIEF_DATE = re.compile(r"【晨間摘要】(\d{4}-\d{2}-\d{2}(?:（[^）]*）)?)")
_DRIVER_LINE = re.compile(r"(\d+)\.\s*(.*)")
_DRIVERS_HEAD = "### Top 3 Drivers"
_BRIEF_DRIVERS = 3
_FACT_END = " → "


def _brief_parts(report: str) -> tuple[str, list[str]]:
    """(the headline, the numbered driver lines) of the morning-brief report."""
    lines = report.splitlines()
    head = next((ln.strip("*") for ln in lines if ln.startswith("**【晨間摘要】")), "晨間摘要")
    drivers: list[str] = []
    in_block = False
    for ln in lines:
        if ln.startswith(_DRIVERS_HEAD):
            in_block = True
        elif ln.startswith("###"):
            in_block = False
        elif in_block and ln[:2].rstrip(".").isdigit():
            drivers.append(ln)
    return head, drivers[:_BRIEF_DRIVERS]


def brief_preview(report: str) -> str:
    """The notification carries the lines that matter — the date headline and the top drivers —
    not the whole report (which, with its tables, lives in the thread the notification opens)."""
    head, drivers = _brief_parts(report)
    return "\n".join([head, *drivers])


def brief_layout(report: str) -> Layout:
    """The morning brief as one heading with its date, each top driver reduced to the fact before
    its explanation, and a pointer to the full report."""
    head, drivers = _brief_parts(report)
    found = _BRIEF_DATE.search(head)
    heading = f"🌅 晨間摘要 {found.group(1)}" if found else "🌅 晨間摘要"
    facts = tuple(f"• {_driver_fact(line)}" for line in drivers)
    return Layout(
        blocks=(LayoutBlock(head=heading, lines=facts), LayoutBlock(lines=(_OPEN_REPORT,)))
    )


def _driver_fact(line: str) -> str:
    numbered = _DRIVER_LINE.fullmatch(line.strip())
    text = numbered.group(2) if numbered else line.strip()
    return text.split(_FACT_END, 1)[0].strip()


DIGEST_MAX_LINES = 8


def digest_body(titles: Sequence[str], alerts: int, jobs: int) -> str:
    """The morning recap, in fact phrasing with the non-advice footer. It lists the titles of what
    fired in the last 24 hours (each already a fixed, scanned fact line) and the active counts."""
    lines: list[str] = []
    if titles:
        lines.append(f"過去 24 小時共 {len(titles)} 則通知：")
        lines.extend(f"・{title}" for title in titles[-DIGEST_MAX_LINES:])
        if len(titles) > DIGEST_MAX_LINES:
            lines.append(f"…另有 {len(titles) - DIGEST_MAX_LINES} 則較早的通知，請見通知紀錄。")
    else:
        lines.append("過去 24 小時沒有新的通知。")
    lines.append(f"目前啟用中：{alerts} 個提醒、{jobs} 個排程。")
    body = "\n".join(lines) + f"\n{NON_ADVICE_FOOTER}"
    if check_output(body, has_market_data=False).reason == "trading_language_redacted":
        return f"晨間摘要：目前啟用中 {alerts} 個提醒、{jobs} 個排程。\n{NON_ADVICE_FOOTER}"
    return body


def digest_layout(titles: Sequence[str], alerts: int, jobs: int) -> Layout:
    """The recap as a heading with the notification count, one line per title (newest last), and
    the active counts."""
    shown = [f"• {title}" for title in titles[-DIGEST_MAX_LINES:]]
    if len(titles) > DIGEST_MAX_LINES:
        shown.append(f"…另有 {len(titles) - DIGEST_MAX_LINES} 則較早的通知，請見通知紀錄")
    heading = f"🗒 過去 24 小時 {len(titles)} 則通知" if titles else "🗒 過去 24 小時沒有新的通知"
    return Layout(
        blocks=(
            LayoutBlock(head=heading, lines=tuple(shown)),
            LayoutBlock(lines=(f"目前啟用中：{alerts} 個提醒、{jobs} 個排程",)),
        )
    )


__all__ = [
    "DIGEST_MAX_LINES",
    "alert_layout",
    "brief_layout",
    "brief_preview",
    "digest_body",
    "digest_layout",
    "plain_lines",
    "report_layout",
    "report_preview",
    "safe_alert_message",
]
