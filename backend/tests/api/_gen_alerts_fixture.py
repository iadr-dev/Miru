"""One-off generator (run explicitly by path, not collected): captures REAL alerts/notifications
API responses into web/src/types/__fixtures__/alerts-api.json so the frontend's Zod schemas are
contract-tested against what the backend actually returns, not against hand-written guesses.

Regenerate with:  pytest tests/api/_gen_alerts_fixture.py -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from agent.loop import _build_card
from config.settings import Settings
from schemas.alerts import PER_STOCK_KINDS, TW_ONLY_KINDS
from schemas.enums import Tier
from tests.api.conftest import sign_in
from tools.base import ToolContext
from tools.propose_alert_tools import ProposeAlertInput, ProposeAlertTool

OUT = (
    Path(__file__).resolve().parents[3]
    / "web"
    / "src"
    / "types"
    / "__fixtures__"
    / "alerts-api.json"
)


def _proposal_card() -> dict[str, object]:
    args = ProposeAlertInput.model_validate(
        {
            "symbol": "2330",
            "market": "TW",
            "condition": {
                "kind": "institutional_streak",
                "investor": "foreign",
                "direction": "buy",
                "days": 3,
            },
        }
    )
    output = asyncio.run(ProposeAlertTool().run(args, ToolContext()))
    return dict(_build_card("alert_proposal", output).data)


def test_generate(client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.FREE)

    def post(path: str, body: dict[str, object]) -> object:
        return client.post(f"/api/v1{path}", json=body, headers=headers).json()

    def get(path: str) -> object:
        return client.get(f"/api/v1{path}", headers=headers).json()

    conditions = [
        {"kind": "close_price", "comparator": "above", "value": 600.5},
        {"kind": "daily_change", "direction": "up", "percent": 5},
        {"kind": "volume_surge", "multiple": 2.5},
        {"kind": "institutional_streak", "investor": "foreign", "direction": "buy", "days": 3},
        {"kind": "attention_entry"},
        {"kind": "disposition_entry"},
        {"kind": "month_revenue_published"},
        {"kind": "margin_balance_change", "side": "margin", "direction": "up", "percent": 10},
        {"kind": "etf_premium_discount", "direction": "premium", "percent": 1},
    ]
    for condition in conditions:
        post("/alerts", {"symbol": "2330", "market": "TW", "condition": condition})
    client.post(
        "/api/v1/schedules",
        json={
            "trigger": {"type": "market_event", "spec": "after_close", "market": "TW"},
            "action": {"skill_ref": "daily-context-brief", "inputs": {}},
            "delivery_channels": ["in_app"],
        },
        headers=headers,
    )
    fixture = {
        "alerts": get("/alerts"),
        "schedules": get("/schedules"),
        "preferences_default": get("/me/alert-preferences"),
        "badge": get("/notifications/badge"),
        "notifications": get("/notifications"),
        "mark_read": post("/notifications/read", {"all": True}),
        # The real `alert_proposal` card the agent emits, built through the same governance scan
        # (`_build_card`) the chat loop uses — so the frontend card is tested against truth.
        "proposal_card": _proposal_card(),
        # The backend's Taiwan-only template set, so the frontend can assert its own copy matches.
        "tw_only_kinds": sorted(TW_ONLY_KINDS),
        "per_stock_kinds": sorted(PER_STOCK_KINDS),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert len(fixture["alerts"]["alerts"]) == len(conditions)  # type: ignore[index,arg-type]
