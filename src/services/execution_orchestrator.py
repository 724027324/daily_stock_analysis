# -*- coding: utf-8 -*-
"""Orchestrate DecisionSignal -> OrderIntent -> confirm/auto -> broker submit."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Dict, Optional

from src.brokers.base import BrokerExecutionPort, BrokerOrderRequest
from src.brokers.paper import PaperBrokerAdapter
from src.config import Config, get_config
from src.repositories.execution_intent_repo import ExecutionIntentRepository
from src.schemas.execution import (
    CANCELABLE_STATUSES,
    CONFIRMABLE_STATUSES,
    CONFIRM_MODES,
)
from src.services.execution_intent_service import (
    ExecutionIntentMappingError,
    ExecutionIntentService,
)
from src.services.execution_risk_gate import ExecutionRiskGate
from src.services.portfolio_service import PortfolioService
from src.storage import utc_naive_now


logger = logging.getLogger(__name__)


class ExecutionOrchestratorError(RuntimeError):
    """Domain error for execution orchestration."""


class ExecutionOrchestrator:
    def __init__(
        self,
        *,
        config: Optional[Config] = None,
        repo: Optional[ExecutionIntentRepository] = None,
        intent_service: Optional[ExecutionIntentService] = None,
        risk_gate: Optional[ExecutionRiskGate] = None,
        portfolio_service: Optional[PortfolioService] = None,
        broker: Optional[BrokerExecutionPort] = None,
    ) -> None:
        self.config = config or get_config()
        self.repo = repo or ExecutionIntentRepository()
        self.intent_service = intent_service or ExecutionIntentService(config=self.config)
        self.risk_gate = risk_gate or ExecutionRiskGate(config=self.config, repo=self.repo)
        self.portfolio_service = portfolio_service or PortfolioService()
        self._broker_override = broker

    def handle_signal_created(self, signal_item: Dict[str, Any], *, created: bool) -> Optional[Dict[str, Any]]:
        """Best-effort entry from analysis pipeline. Never raises to caller."""

        try:
            if not isinstance(signal_item, dict) or signal_item.get("id") is None:
                return None
            if str(signal_item.get("status") or "") != "active":
                return None
            # Duplicates still may need no new order; only new/refreshed actives create intents.
            if not created and not bool(getattr(self.config, "execution_allow_duplicate_signal_intents", False)):
                existing = self.repo.list(signal_id=int(signal_item["id"]), page=1, page_size=1)
                if existing[1] > 0:
                    return self._serialize(existing[0][0]) if existing[0] else None
            return self.create_intent_from_signal(signal_item)
        except Exception as exc:
            logger.warning(
                "execution orchestrator skipped signal_id=%s error=%s",
                signal_item.get("id") if isinstance(signal_item, dict) else None,
                exc,
                exc_info=True,
            )
            return None

    def create_intent_from_signal(self, signal_item: Dict[str, Any]) -> Dict[str, Any]:
        confirm_mode = self._confirm_mode()
        live_enabled = bool(getattr(self.config, "live_trading_enabled", False))
        broker_name = self._broker_name(live_enabled=live_enabled)

        try:
            mapped = self.intent_service.map_signal_to_order_fields(signal_item)
        except ExecutionIntentMappingError as exc:
            return self._persist_rejected_stub(
                signal_item,
                confirm_mode=confirm_mode,
                broker_name=broker_name,
                live_enabled=live_enabled,
                reason=str(exc),
            )

        gate = self.risk_gate.evaluate(
            signal=signal_item,
            side=mapped["side"],
            quantity=mapped["quantity"],
            limit_price=mapped["limit_price"],
            notional_cny=mapped["notional_cny"],
            idempotency_key=mapped["idempotency_key"],
            require_live_session=live_enabled and confirm_mode == "auto",
            avg_cost=mapped.get("avg_cost"),
        )
        if not gate.allowed:
            return self._persist_rejected_from_mapped(
                mapped,
                confirm_mode=confirm_mode,
                broker_name=broker_name,
                live_enabled=live_enabled,
                reason=gate.reason,
                details=gate.details,
            )

        existing = self.repo.get_by_idempotency_key(mapped["idempotency_key"])
        if existing is not None:
            return self._serialize(existing)

        status = "pending_confirm" if confirm_mode == "manual" else "auto_submit"
        row = self.repo.create(
            {
                "signal_id": mapped["signal_id"],
                "stock_code": mapped["stock_code"],
                "stock_name": mapped.get("stock_name"),
                "market": mapped["market"],
                "action": mapped["action"],
                "side": mapped["side"],
                "quantity": mapped["quantity"],
                "limit_price": mapped["limit_price"],
                "notional_cny": mapped["notional_cny"],
                "status": status,
                "confirm_mode": confirm_mode,
                "broker": broker_name,
                "idempotency_key": mapped["idempotency_key"],
                "portfolio_account_id": mapped.get("portfolio_account_id"),
                "live_enabled": live_enabled,
                "metadata_json": json.dumps(mapped.get("metadata") or {}, ensure_ascii=False),
            }
        )
        serialized = self._serialize(row)
        if confirm_mode == "auto":
            return self.submit_intent(int(row.id), source="auto")
        return serialized

    def list_intents(
        self,
        *,
        status: Optional[str] = None,
        stock_code: Optional[str] = None,
        signal_id: Optional[int] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        rows, total = self.repo.list(
            status=status,
            stock_code=stock_code,
            signal_id=signal_id,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [self._serialize(row) for row in rows],
            "total": total,
            "page": max(1, int(page)),
            "page_size": max(1, min(int(page_size), 100)),
        }

    def get_intent(self, intent_id: int) -> Dict[str, Any]:
        row = self.repo.get(intent_id)
        if row is None:
            raise ExecutionOrchestratorError("intent_not_found")
        return self._serialize(row)

    def confirm_intent(self, intent_id: int) -> Dict[str, Any]:
        row = self.repo.get(intent_id)
        if row is None:
            raise ExecutionOrchestratorError("intent_not_found")
        if row.status not in CONFIRMABLE_STATUSES:
            raise ExecutionOrchestratorError(f"intent_not_confirmable:{row.status}")
        return self.submit_intent(intent_id, source="manual_confirm")

    def reject_intent(self, intent_id: int, *, reason: str = "user_rejected") -> Dict[str, Any]:
        row = self.repo.get(intent_id)
        if row is None:
            raise ExecutionOrchestratorError("intent_not_found")
        if row.status not in CONFIRMABLE_STATUSES:
            raise ExecutionOrchestratorError(f"intent_not_rejectable:{row.status}")
        updated = self.repo.update_fields(
            intent_id,
            {"status": "rejected", "reject_reason": (reason or "user_rejected")[:256]},
        )
        return self._serialize(updated)

    def cancel_intent(self, intent_id: int, *, reason: str = "user_cancelled") -> Dict[str, Any]:
        row = self.repo.get(intent_id)
        if row is None:
            raise ExecutionOrchestratorError("intent_not_found")
        if row.status not in CANCELABLE_STATUSES:
            raise ExecutionOrchestratorError(f"intent_not_cancelable:{row.status}")
        updated = self.repo.update_fields(
            intent_id,
            {"status": "cancelled", "reject_reason": (reason or "user_cancelled")[:256]},
        )
        return self._serialize(updated)

    def submit_intent(self, intent_id: int, *, source: str) -> Dict[str, Any]:
        row = self.repo.get(intent_id)
        if row is None:
            raise ExecutionOrchestratorError("intent_not_found")
        if row.status in {"submitted", "rejected", "cancelled", "failed"}:
            return self._serialize(row)

        live_enabled = bool(row.live_enabled)
        # Re-check live session on actual submit
        meta = _meta(row)
        gate = self.risk_gate.evaluate(
            signal={
                "id": row.signal_id,
                "stock_code": row.stock_code,
                "market": row.market,
                "action": row.action,
                "entry_low": meta.get("entry_low"),
                "entry_high": meta.get("entry_high"),
            },
            side=row.side,
            quantity=int(row.quantity),
            limit_price=float(row.limit_price),
            notional_cny=float(row.notional_cny or (row.quantity * row.limit_price)),
            idempotency_key=f"{row.idempotency_key}#submit",
            require_live_session=live_enabled,
            avg_cost=meta.get("avg_cost"),
        )
        # Duplicate submit key is expected unique per submit marker; ignore duplicate reason for submit marker.
        if not gate.allowed and gate.reason != "duplicate_idempotency_key":
            updated = self.repo.update_fields(
                intent_id,
                {"status": "failed", "reject_reason": gate.reason[:256]},
            )
            return self._serialize(updated)

        broker = self._broker_override or self._build_broker(live_enabled=live_enabled)
        request = BrokerOrderRequest(
            stock_code=row.stock_code,
            side=row.side,
            quantity=int(row.quantity),
            limit_price=float(row.limit_price),
            idempotency_key=row.idempotency_key,
            stock_name=row.stock_name,
        )
        try:
            result = broker.place_order(request)
        except Exception as exc:
            logger.error("broker place_order failed intent_id=%s: %s", intent_id, exc, exc_info=True)
            updated = self.repo.update_fields(
                intent_id,
                {"status": "failed", "reject_reason": f"broker_exception:{exc}"[:256]},
            )
            return self._serialize(updated)

        if not result.success:
            updated = self.repo.update_fields(
                intent_id,
                {
                    "status": "failed",
                    "reject_reason": (result.message or "broker_rejected")[:256],
                },
            )
            return self._serialize(updated)

        updated = self.repo.update_fields(
            intent_id,
            {
                "status": "submitted",
                "broker_order_id": result.broker_order_id,
                "submitted_at": utc_naive_now(),
                "reject_reason": None,
                "metadata_json": json.dumps(
                    {
                        **_meta(row),
                        "submit_source": source,
                        "broker_message": result.message,
                        "broker_raw": result.raw,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            },
        )
        self._maybe_sync_portfolio_ledger(updated)
        return self._serialize(updated)

    def _maybe_sync_portfolio_ledger(self, row: Any) -> None:
        if not bool(getattr(self.config, "execution_sync_portfolio_ledger", False)):
            return
        account_id = getattr(row, "portfolio_account_id", None)
        if account_id is None:
            logger.info("skip portfolio ledger sync: no portfolio_account_id intent_id=%s", row.id)
            return
        try:
            self.portfolio_service.record_trade(
                account_id=int(account_id),
                symbol=row.stock_code,
                trade_date=date.today(),
                side=row.side,
                quantity=float(row.quantity),
                price=float(row.limit_price),
                market="cn",
                currency="CNY",
                trade_uid=row.broker_order_id or row.idempotency_key,
                dedup_hash=row.idempotency_key,
                note=f"execution_intent:{row.id}",
            )
        except Exception as exc:
            logger.warning(
                "portfolio ledger sync failed intent_id=%s: %s",
                getattr(row, "id", None),
                exc,
                exc_info=True,
            )

    def _build_broker(self, *, live_enabled: bool) -> BrokerExecutionPort:
        if not live_enabled:
            return PaperBrokerAdapter()
        broker = str(getattr(self.config, "execution_broker", "tdx") or "tdx").strip().lower()
        if broker == "paper":
            return PaperBrokerAdapter()
        if broker == "tdx":
            from src.brokers.tdx.execution import TongdaxinExecutionAdapter

            return TongdaxinExecutionAdapter(
                client_path=getattr(self.config, "tdx_client_path", None),
                account_id=getattr(self.config, "tdx_account_id", None),
            )
        raise ExecutionOrchestratorError(f"unsupported_broker:{broker}")

    def _confirm_mode(self) -> str:
        mode = str(getattr(self.config, "execution_confirm_mode", "manual") or "manual").strip().lower()
        if mode not in CONFIRM_MODES:
            return "manual"
        return mode

    def _broker_name(self, *, live_enabled: bool) -> str:
        if not live_enabled:
            return "paper"
        broker = str(getattr(self.config, "execution_broker", "tdx") or "tdx").strip().lower()
        return broker if broker else "tdx"

    def _persist_rejected_stub(
        self,
        signal_item: Dict[str, Any],
        *,
        confirm_mode: str,
        broker_name: str,
        live_enabled: bool,
        reason: str,
    ) -> Dict[str, Any]:
        signal_id = int(signal_item["id"])
        action = str(signal_item.get("action") or "watch")
        key = f"exec-{signal_id}-rejected-{reason}"[:120]
        existing = self.repo.get_by_idempotency_key(key)
        if existing is not None:
            return self._serialize(existing)
        row = self.repo.create(
            {
                "signal_id": signal_id,
                "stock_code": str(signal_item.get("stock_code") or ""),
                "stock_name": signal_item.get("stock_name"),
                "market": str(signal_item.get("market") or "cn"),
                "action": action,
                "side": "buy",
                "quantity": 0,
                "limit_price": 0.0,
                "notional_cny": 0.0,
                "status": "rejected",
                "confirm_mode": confirm_mode,
                "broker": broker_name,
                "idempotency_key": key,
                "reject_reason": reason[:256],
                "live_enabled": live_enabled,
                "metadata_json": "{}",
            }
        )
        return self._serialize(row)

    def _persist_rejected_from_mapped(
        self,
        mapped: Dict[str, Any],
        *,
        confirm_mode: str,
        broker_name: str,
        live_enabled: bool,
        reason: str,
        details: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        key = mapped["idempotency_key"]
        existing = self.repo.get_by_idempotency_key(key)
        if existing is not None:
            return self._serialize(existing)
        meta = dict(mapped.get("metadata") or {})
        if details:
            meta["gate_details"] = details
        row = self.repo.create(
            {
                "signal_id": mapped["signal_id"],
                "stock_code": mapped["stock_code"],
                "stock_name": mapped.get("stock_name"),
                "market": mapped["market"],
                "action": mapped["action"],
                "side": mapped["side"],
                "quantity": mapped["quantity"],
                "limit_price": mapped["limit_price"],
                "notional_cny": mapped["notional_cny"],
                "status": "rejected",
                "confirm_mode": confirm_mode,
                "broker": broker_name,
                "idempotency_key": key,
                "reject_reason": reason[:256],
                "portfolio_account_id": mapped.get("portfolio_account_id"),
                "live_enabled": live_enabled,
                "metadata_json": json.dumps(meta, ensure_ascii=False),
            }
        )
        return self._serialize(row)

    @staticmethod
    def _serialize(row: Any) -> Dict[str, Any]:
        return {
            "id": int(row.id),
            "signal_id": int(row.signal_id),
            "stock_code": row.stock_code,
            "stock_name": row.stock_name,
            "market": row.market,
            "action": row.action,
            "side": row.side,
            "quantity": int(row.quantity or 0),
            "limit_price": float(row.limit_price or 0.0),
            "notional_cny": float(row.notional_cny or 0.0) if row.notional_cny is not None else None,
            "status": row.status,
            "confirm_mode": row.confirm_mode,
            "broker": row.broker,
            "idempotency_key": row.idempotency_key,
            "reject_reason": row.reject_reason,
            "broker_order_id": row.broker_order_id,
            "portfolio_account_id": row.portfolio_account_id,
            "live_enabled": bool(row.live_enabled),
            "metadata": _meta(row),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
        }


def _meta(row: Any) -> Dict[str, Any]:
    raw = getattr(row, "metadata_json", None)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
