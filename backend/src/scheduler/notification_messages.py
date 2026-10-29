"""What a rule-alert, scheduled-report, morning-brief or digest notification looks like on a phone.

Each builder writes the `Layout` that sits next to the plain body the in-app inbox and email show,
from the same facts, so the two cannot drift. Pure functions, no I/O. Copy is fact phrasing only
and every text a builder returns is scanned by the caller before it is sent.
"""

from __future__ import annotations

from datetime import date

from scheduler.delivery.layout import Layout, LayoutBlock
from scheduler.evaluator import Evaluation
from scheduler.strategy_messages import safe_message


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


__all__ = ["alert_layout", "safe_alert_message"]
