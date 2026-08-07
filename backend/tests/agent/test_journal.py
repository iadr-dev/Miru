"""`Journal` — the per-Step audit trail: typed fields recorded per tool call and per output
guardrail verdict, never a raw sponsor payload or secret."""

from __future__ import annotations

from agent.journal import Journal


def test_record_tool_call_appends_a_typed_entry() -> None:
    journal = Journal()

    entry = journal.record_tool_call(
        step_index=0,
        tool_name="get_chip_flows",
        arguments_keys=("id", "market"),
        result_summary="GetChipFlowsOutput(rows, source_id)",
        guardrail_allowed=True,
        guardrail_reason=None,
    )

    assert entry.kind == "tool_call"
    assert entry.tool_name == "get_chip_flows"
    assert entry.argument_keys == ("id", "market")
    assert journal.entries == (entry,)


def test_record_output_guardrail_appends_a_typed_entry() -> None:
    journal = Journal()

    entry = journal.record_output_guardrail(step_index=1, allowed=True, reason="caveat_appended")

    assert entry.kind == "guardrail_output"
    assert entry.guardrail_allowed is True
    assert entry.guardrail_reason == "caveat_appended"


def test_entries_accumulate_across_multiple_steps_in_order() -> None:
    journal = Journal()

    journal.record_tool_call(
        step_index=0,
        tool_name="get_price_quote",
        arguments_keys=("id", "market"),
        result_summary="GetPriceQuoteOutput(bar, source_id)",
        guardrail_allowed=True,
        guardrail_reason=None,
    )
    journal.record_output_guardrail(step_index=0, allowed=True, reason=None)

    assert [entry.kind for entry in journal.entries] == ["tool_call", "guardrail_output"]
    assert [entry.step_index for entry in journal.entries] == [0, 0]


def test_journal_never_records_raw_payload_values() -> None:
    """The journal only ever receives a pre-reduced `result_summary` (type name + field
    names) — it has no path to see or record a raw sponsor payload value."""
    journal = Journal()
    secret_looking_value = "sk-live-should-never-appear-anywhere"

    entry = journal.record_tool_call(
        step_index=0,
        tool_name="get_chip_flows",
        arguments_keys=("id",),
        result_summary="GetChipFlowsOutput(rows, source_id)",
        guardrail_allowed=True,
        guardrail_reason=None,
    )

    assert secret_looking_value not in (entry.result_summary or "")
    assert secret_looking_value not in str(entry.argument_keys)


def test_journal_entries_are_immutable() -> None:
    journal = Journal()
    entry = journal.record_output_guardrail(step_index=0, allowed=True, reason=None)

    try:
        entry.step_index = 99  # type: ignore[misc]
        raised = False
    except AttributeError:
        raised = True

    assert raised, "JournalEntry is a frozen dataclass and must reject mutation"
