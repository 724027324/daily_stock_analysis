# -*- coding: utf-8 -*-
"""Map DecisionSignal payloads into sized execution order intents."""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Any, Dict, Optional, Tuple

from src.config import Config, get_config
from src.schemas.execution import ACTION_TO_SIDE, DEFAULT_ALLOWED_ACTIONS
from src.services.portfolio_service import PortfolioService


logger = logging.getLogger(__name__)


class ExecutionIntentMappingError(ValueError):
    """Raised when a signal cannot be mapped into an executable intent."""


class ExecutionIntentService:
    def __init__(
        self,
        *,
        config: Optional[Config] = None,
        portfolio_service: Optional[PortfolioService] = None,
    ) -> None:
        self.config = config or get_config()
        self.portfolio_service = portfolio_service or PortfolioService()

    def map_signal_to_order_fields(
        self,
        signal: Dict[str, Any],
        *,
        account_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        action = str(signal.get("action") or "").strip().lower()
        allowed = self._allowed_actions()
        if action not in allowed:
            raise ExecutionIntentMappingError(f"action_not_tradable:{action}")

        market = str(signal.get("market") or "").strip().lower()
        if market != "cn":
            raise ExecutionIntentMappingError(f"market_not_cn:{market}")

        side = ACTION_TO_SIDE.get(action)
        if side is None:
            raise ExecutionIntentMappingError(f"action_has_no_side:{action}")

        stock_code = str(signal.get("stock_code") or "").strip()
        if not stock_code:
            raise ExecutionIntentMappingError("stock_code_required")

        limit_price = self._limit_price(signal, action=action)
        lot_size = _lot_size_for_code(stock_code)

        snapshot_account_id, available_cash, held_qty = self._resolve_account_context(
            stock_code=stock_code,
            account_id=account_id,
        )

        if side == "buy":
            quantity = self._buy_quantity(
                available_cash=available_cash,
                limit_price=limit_price,
                lot_size=lot_size,
            )
        else:
            quantity = self._sell_quantity(action=action, held_qty=held_qty, lot_size=lot_size)

        if quantity <= 0:
            raise ExecutionIntentMappingError("quantity_resolved_to_zero")

        notional = float(quantity) * float(limit_price)
        signal_id = int(signal["id"])
        idempotency_key = build_idempotency_key(
            signal_id=signal_id,
            action=action,
            side=side,
            quantity=quantity,
            limit_price=limit_price,
        )
        return {
            "signal_id": signal_id,
            "stock_code": stock_code,
            "stock_name": signal.get("stock_name"),
            "market": market,
            "action": action,
            "side": side,
            "quantity": int(quantity),
            "limit_price": float(limit_price),
            "notional_cny": round(notional, 4),
            "idempotency_key": idempotency_key,
            "portfolio_account_id": snapshot_account_id,
            "metadata": {
                "lot_size": lot_size,
                "available_cash": available_cash,
                "held_qty": held_qty,
                "entry_low": signal.get("entry_low"),
                "entry_high": signal.get("entry_high"),
            },
        }

    def _allowed_actions(self) -> set[str]:
        raw = getattr(self.config, "execution_allowed_actions", None)
        if not raw:
            return set(DEFAULT_ALLOWED_ACTIONS)
        return {part.strip().lower() for part in str(raw).split(",") if part.strip()}

    def _limit_price(self, signal: Dict[str, Any], *, action: str) -> float:
        entry_low = signal.get("entry_low")
        entry_high = signal.get("entry_high")
        if action in {"buy", "add"}:
            if entry_low is None or entry_high is None:
                if bool(getattr(self.config, "execution_require_entry_band", True)):
                    raise ExecutionIntentMappingError("entry_band_required")
                raise ExecutionIntentMappingError("entry_band_missing")
            low = float(entry_low)
            high = float(entry_high)
            if low <= 0 or high <= 0 or low > high:
                raise ExecutionIntentMappingError("entry_band_invalid")
            return round((low + high) / 2.0, 4)

        # Sell / reduce: prefer entry mid as protective limit, else target/stop heuristics.
        candidates = []
        for key in ("entry_high", "entry_low", "target_price", "stop_loss"):
            value = signal.get(key)
            if value is not None:
                try:
                    price = float(value)
                except (TypeError, ValueError):
                    continue
                if price > 0:
                    candidates.append(price)
        if not candidates:
            raise ExecutionIntentMappingError("sell_price_unavailable")
        return round(sum(candidates) / len(candidates), 4)

    def _buy_quantity(self, *, available_cash: float, limit_price: float, lot_size: int) -> int:
        max_notional = getattr(self.config, "execution_max_order_notional_cny", None)
        max_pct = getattr(self.config, "execution_max_position_pct", None)
        if max_notional is None or max_pct is None:
            raise ExecutionIntentMappingError("risk_limits_not_configured")
        budget_by_pct = available_cash * (float(max_pct) / 100.0)
        budget = min(float(max_notional), budget_by_pct, available_cash)
        if budget <= 0 or limit_price <= 0:
            return 0
        raw_shares = math.floor(budget / float(limit_price))
        return (raw_shares // lot_size) * lot_size

    def _sell_quantity(self, *, action: str, held_qty: float, lot_size: int) -> int:
        held_lots = int(math.floor(held_qty / lot_size) * lot_size)
        if held_lots <= 0:
            raise ExecutionIntentMappingError("no_position_to_sell")
        if action == "sell":
            return held_lots
        # reduce: sell half of sellable lots, at least one lot when possible
        half = (held_lots // (2 * lot_size)) * lot_size
        return half if half > 0 else min(lot_size, held_lots)

    def _resolve_account_context(
        self,
        *,
        stock_code: str,
        account_id: Optional[int],
    ) -> Tuple[Optional[int], float, float]:
        configured = getattr(self.config, "execution_portfolio_account_id", None)
        target_id = account_id if account_id is not None else configured
        try:
            snapshot = self.portfolio_service.get_portfolio_snapshot(
                account_id=int(target_id) if target_id is not None else None,
                cost_method="fifo",
                include_realtime=False,
                read_only=True,
            )
        except Exception as exc:
            logger.warning("portfolio snapshot unavailable for execution sizing: %s", exc)
            raise ExecutionIntentMappingError("portfolio_snapshot_unavailable") from exc

        accounts = snapshot.get("accounts") or []
        if target_id is not None:
            account = next(
                (
                    item
                    for item in accounts
                    if int(item.get("account_id") or item.get("id") or 0) == int(target_id)
                ),
                None,
            )
            if account is None and accounts:
                available_cash = float(snapshot.get("total_cash") or 0.0)
                held_qty = _held_qty_from_snapshot(snapshot, stock_code)
                return int(target_id), available_cash, held_qty
            if account is None:
                raise ExecutionIntentMappingError("portfolio_account_not_found")
            available_cash = float(account.get("total_cash") or account.get("cash") or 0.0)
            held_qty = _held_qty_from_account(account, stock_code)
            return int(account.get("account_id") or account.get("id") or target_id), available_cash, held_qty

        if not accounts:
            return None, 0.0, 0.0

        account = next(
            (
                item
                for item in accounts
                if str(item.get("market") or "").lower() in {"cn", "a", ""}
                or str(item.get("base_currency") or item.get("currency") or "").upper() == "CNY"
            ),
            accounts[0],
        )
        available_cash = float(account.get("total_cash") or account.get("cash") or 0.0)
        held_qty = _held_qty_from_account(account, stock_code)
        account_key = account.get("account_id") if account.get("account_id") is not None else account.get("id")
        return (
            int(account_key) if account_key is not None else None,
            available_cash,
            held_qty,
        )


def build_idempotency_key(
    *,
    signal_id: int,
    action: str,
    side: str,
    quantity: int,
    limit_price: float,
) -> str:
    raw = f"{signal_id}|{action}|{side}|{quantity}|{limit_price:.4f}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"exec-{signal_id}-{digest}"


def _lot_size_for_code(stock_code: str) -> int:
    digits = "".join(ch for ch in stock_code if ch.isdigit())
    code6 = digits[-6:] if len(digits) >= 6 else digits
    if code6.startswith(("688", "689")):
        return 200
    return 100


def _held_qty_from_snapshot(snapshot: Dict[str, Any], stock_code: str) -> float:
    for account in snapshot.get("accounts") or []:
        qty = _held_qty_from_account(account, stock_code)
        if qty > 0:
            return qty
    return 0.0


def _held_qty_from_account(account: Dict[str, Any], stock_code: str) -> float:
    target = _normalize_code(stock_code)
    for position in account.get("positions") or account.get("holdings") or []:
        code = _normalize_code(str(position.get("symbol") or position.get("stock_code") or ""))
        if code == target:
            try:
                return float(position.get("quantity") or position.get("qty") or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _normalize_code(code: str) -> str:
    value = (code or "").strip().upper()
    for suffix in (".SH", ".SZ", ".SS", ".BJ"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else value
