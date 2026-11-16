# -*- coding: utf-8 -*-
"""Tests for the A-share execution intent layer."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from src.brokers.paper import PaperBrokerAdapter
from src.config import Config
from src.services.execution_orchestrator import ExecutionOrchestrator
from src.services.execution_risk_gate import ExecutionRiskGate
from src.services.portfolio_service import PortfolioService
from src.storage import DatabaseManager


@pytest.fixture()
def isolated_execution_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "execution_layer.db"
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"DATABASE_PATH={db_path}",
                "LIVE_TRADING_ENABLED=false",
                "EXECUTION_CONFIRM_MODE=manual",
                "EXECUTION_BROKER=paper",
                "EXECUTION_MAX_ORDER_NOTIONAL_CNY=50000",
                "EXECUTION_MAX_POSITION_PCT=20",
                "EXECUTION_REQUIRE_ENTRY_BAND=true",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ENV_FILE", str(env_path))
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("EXECUTION_CONFIRM_MODE", "manual")
    monkeypatch.setenv("EXECUTION_BROKER", "paper")
    monkeypatch.setenv("EXECUTION_MAX_ORDER_NOTIONAL_CNY", "50000")
    monkeypatch.setenv("EXECUTION_MAX_POSITION_PCT", "20")
    monkeypatch.setenv("EXECUTION_REQUIRE_ENTRY_BAND", "true")
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()


def _signal(**overrides):
    payload = {
        "id": 101,
        "stock_code": "000001",
        "stock_name": "平安银行",
        "market": "cn",
        "action": "buy",
        "status": "active",
        "entry_low": 8.0,
        "entry_high": 10.0,
    }
    payload.update(overrides)
    return payload


def _seed_cash_account(amount: float = 500000.0) -> int:
    service = PortfolioService()
    account = service.create_account(name="CN-cash", broker="Demo", market="cn", base_currency="CNY")
    aid = int(account["id"])
    service.record_cash_ledger(
        account_id=aid,
        event_date=date(2026, 1, 1),
        direction="in",
        amount=amount,
        currency="CNY",
    )
    return aid


def test_risk_gate_fails_closed_without_notional_limit(isolated_execution_env, monkeypatch):
    monkeypatch.setenv("EXECUTION_MAX_ORDER_NOTIONAL_CNY", "")
    Config.reset_instance()
    gate = ExecutionRiskGate(config=Config.get_instance())
    result = gate.evaluate(
        signal=_signal(),
        side="buy",
        quantity=100,
        limit_price=1450.0,
        notional_cny=145000.0,
        idempotency_key="k1",
        require_live_session=False,
    )
    assert result.allowed is False
    assert result.reason == "max_notional_not_configured"


def test_risk_gate_rejects_non_cn_market(isolated_execution_env):
    gate = ExecutionRiskGate(config=Config.get_instance())
    result = gate.evaluate(
        signal=_signal(market="us"),
        side="buy",
        quantity=100,
        limit_price=10.0,
        notional_cny=1000.0,
        idempotency_key="k-us",
        require_live_session=False,
    )
    assert result.allowed is False
    assert result.reason == "market_not_allowed"


def test_manual_mode_creates_pending_confirm_and_confirm_submits_paper(isolated_execution_env, monkeypatch):
    account_id = _seed_cash_account()
    monkeypatch.setenv("EXECUTION_PORTFOLIO_ACCOUNT_ID", str(account_id))
    Config.reset_instance()
    paper = PaperBrokerAdapter(starting_cash=1_000_000)
    orch = ExecutionOrchestrator(config=Config.get_instance(), broker=paper)

    created = orch.create_intent_from_signal(_signal())
    assert created["status"] == "pending_confirm"
    assert created["broker"] == "paper"
    assert created["quantity"] > 0

    confirmed = orch.confirm_intent(created["id"])
    assert confirmed["status"] == "submitted"
    assert confirmed["broker_order_id"]
    assert paper.get_cash().available < 1_000_000


def test_auto_mode_submits_immediately_on_paper(isolated_execution_env, monkeypatch):
    account_id = _seed_cash_account()
    monkeypatch.setenv("EXECUTION_PORTFOLIO_ACCOUNT_ID", str(account_id))
    monkeypatch.setenv("EXECUTION_CONFIRM_MODE", "auto")
    Config.reset_instance()
    paper = PaperBrokerAdapter(starting_cash=1_000_000)
    orch = ExecutionOrchestrator(config=Config.get_instance(), broker=paper)

    created = orch.create_intent_from_signal(_signal(id=202))
    assert created["status"] == "submitted"
    assert created["confirm_mode"] == "auto"


def test_idempotent_intent_key_returns_existing(isolated_execution_env, monkeypatch):
    account_id = _seed_cash_account()
    monkeypatch.setenv("EXECUTION_PORTFOLIO_ACCOUNT_ID", str(account_id))
    Config.reset_instance()
    orch = ExecutionOrchestrator(config=Config.get_instance(), broker=PaperBrokerAdapter())

    first = orch.create_intent_from_signal(_signal(id=303))
    second = orch.create_intent_from_signal(_signal(id=303))
    assert first["id"] == second["id"]
    assert first["idempotency_key"] == second["idempotency_key"]


def test_reject_and_cancel_pending_intent(isolated_execution_env, monkeypatch):
    account_id = _seed_cash_account()
    monkeypatch.setenv("EXECUTION_PORTFOLIO_ACCOUNT_ID", str(account_id))
    Config.reset_instance()
    orch = ExecutionOrchestrator(config=Config.get_instance(), broker=PaperBrokerAdapter())

    pending = orch.create_intent_from_signal(_signal(id=404))
    rejected = orch.reject_intent(pending["id"])
    assert rejected["status"] == "rejected"

    pending2 = orch.create_intent_from_signal(_signal(id=405))
    cancelled = orch.cancel_intent(pending2["id"])
    assert cancelled["status"] == "cancelled"


def test_missing_entry_band_rejects_buy(isolated_execution_env, monkeypatch):
    account_id = _seed_cash_account()
    monkeypatch.setenv("EXECUTION_PORTFOLIO_ACCOUNT_ID", str(account_id))
    Config.reset_instance()
    orch = ExecutionOrchestrator(config=Config.get_instance(), broker=PaperBrokerAdapter())
    result = orch.create_intent_from_signal(_signal(id=506, entry_low=None, entry_high=None))
    assert result["status"] == "rejected"
    assert "entry_band" in (result.get("reject_reason") or "")


def test_api_list_and_confirm(isolated_execution_env, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from api.app import create_app

    account_id = _seed_cash_account()
    monkeypatch.setenv("EXECUTION_PORTFOLIO_ACCOUNT_ID", str(account_id))
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "false")
    Config.reset_instance()

    orch = ExecutionOrchestrator(config=Config.get_instance(), broker=PaperBrokerAdapter())
    created = orch.create_intent_from_signal(_signal(id=707))

    static_dir = tmp_path / "empty-static"
    static_dir.mkdir(exist_ok=True)
    app = create_app(static_dir=static_dir)
    client = TestClient(app)
    listed = client.get("/api/v1/execution/intents", params={"status": "pending_confirm"})
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] >= 1
    assert any(item["id"] == created["id"] for item in body["items"])

    confirmed = client.post(f"/api/v1/execution/intents/{created['id']}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "submitted"
