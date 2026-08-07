from __future__ import annotations

import asyncio

from events.bus import InMemoryEventBus
from events.usage import TokenBreakdown, UsageAccumulator


def test_token_breakdown_total_sums_every_field() -> None:
    breakdown = TokenBreakdown(system_prompt=10, recent_messages=20, memory_block=5)
    assert breakdown.total() == 35


def test_accumulator_tracks_llm_and_tool_calls() -> None:
    accumulator = UsageAccumulator("turn-1")
    accumulator.add_llm_call(input_tokens=100, output_tokens=50, cost_micros=1200)
    accumulator.add_tool_call(is_finmind=True)
    accumulator.add_tool_call(is_finmind=False)

    snapshot = accumulator.snapshot()

    assert snapshot.input_tokens == 100
    assert snapshot.output_tokens == 50
    assert snapshot.cost_micros == 1200
    assert snapshot.tool_calls == 2
    assert snapshot.finmind_calls == 1


async def test_publish_emits_a_usage_event_with_the_current_snapshot() -> None:
    bus = InMemoryEventBus()
    accumulator = UsageAccumulator("turn-1")
    accumulator.add_llm_call(input_tokens=10, output_tokens=5)

    async def collect() -> dict[str, object]:
        async for event in bus.subscribe(["usage"]):
            return event.data
        return {}

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    accumulator.publish(bus)
    received = await task

    assert received["input_tokens"] == 10
    assert received["output_tokens"] == 5
    assert received["turn_id"] == "turn-1"
