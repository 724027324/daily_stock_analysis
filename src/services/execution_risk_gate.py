# -*- coding: utf-8 -*-
"""Fail-closed risk checks for execution intents."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Set

from src.config import Config, get_config
from src.core.trading_calendar import MarketPhase, build_market_phase_context
from src.repositories.execution_intent_repo import ExecutionIntentRepository
from src.schemas.execution import ACTION_TO_SIDE, DEFAULT_ALLOWED_ACTIONS


logger = logging.getLogger(__name__)

_LIVE_OPEN_PHASES = frozenset({
    MarketPhase.INTRADAY.value,
    MarketPhase.CLOSING_AUCTION.value,
})


@dataclass(frozen=True)
class RiskGateResult:
    allowed: bool
    reason: str = ""
    details: Optional[Dict[str, Any]] = None


class ExecutionRiskGate:
    """Validate mapped order intents before confirm/auto submit."""

    def __init__(
        self,
        *,
        config: Optional[Config] = None,
        repo: Optional[ExecutionIntentRepository] = None,
    ) -> None:
        self.config = config or get_config()
        self.repo = repo or ExecutionIntentRepository()

    def evaluate(
        self,
        *,
        signal: Dict[str, Any],
        side: str,
        quantity: int,
        limit_price: float,
        notional_cny: float,
        idempotency_key: str,
        require_live_session: bool,
    ) -> RiskGateResult:
        market = str(signal.get("market") or "").strip().lower()
        action = str(signal.get("action") or "").strip().lower()
        stock_code = str(signal.get("stock_code") or "").strip()

        allowlist = self._market_allowlist()
        if market not in allowlist:
            return RiskGateResult(False, "market_not_allowed", {"market": market})

        allowed_actions = self._allowed_actions()
        if action not in allowed_actions:
            return RiskGateResult(False, "action_not_allowed", {"action": action})

        if ACTION_TO_SIDE.get(action) != side:
            return RiskGateResult(False, "side_action_mismatch")

        if quantity <= 0:
            return RiskGateResult(False, "invalid_quantity")
        if limit_price <= 0 or notional_cny <= 0:
            return RiskGateResult(False, "invalid_price_or_notional")

        max_notional = getattr(self.config, "execution_max_order_notional_cny", None)
        if max_notional is None:
            return RiskGateResult(False, "max_notional_not_configured")
        if notional_cny > float(max_notional) + 1e-9:
            return RiskGateResult(
                False,
                "notional_exceeds_max",
                {"notional": notional_cny, "max": max_notional},
            )

        max_pct = getattr(self.config, "execution_max_position_pct", None)
        if max_pct is None:
            return RiskGateResult(False, "max_position_pct_not_configured")
        if float(max_pct) <= 0 or float(max_pct) > 100:
            return RiskGateResult(False, "max_position_pct_invalid")

        require_band = bool(getattr(self.config, "execution_require_entry_band", True))
        if require_band and action in {"buy", "add"}:
            entry_low = signal.get("entry_low")
            entry_high = signal.get("entry_high")
            if entry_low is None or entry_high is None:
                return RiskGateResult(False, "entry_band_required")
            try:
                if float(entry_low) <= 0 or float(entry_high) <= 0 or float(entry_low) > float(entry_high):
                    return RiskGateResult(False, "entry_band_invalid")
            except (TypeError, ValueError):
                return RiskGateResult(False, "entry_band_invalid")

        board_reason = _special_board_reason(stock_code)
        if board_reason:
            return RiskGateResult(False, board_reason, {"stock_code": stock_code})

        existing = self.repo.get_by_idempotency_key(idempotency_key)
        if existing is not None and existing.status not in {"rejected", "cancelled", "failed"}:
            return RiskGateResult(
                False,
                "duplicate_idempotency_key",
                {"existing_id": existing.id, "status": existing.status},
            )

        if require_live_session:
            session_result = self._check_cn_session_open()
            if not session_result.allowed:
                return session_result

        return RiskGateResult(True, "ok")

    def _market_allowlist(self) -> Set[str]:
        raw = getattr(self.config, "execution_market_allowlist", "cn") or "cn"
        return {part.strip().lower() for part in str(raw).split(",") if part.strip()}

    def _allowed_actions(self) -> Set[str]:
        raw = getattr(self.config, "execution_allowed_actions", None)
        if not raw:
            return set(DEFAULT_ALLOWED_ACTIONS)
        return {part.strip().lower() for part in str(raw).split(",") if part.strip()}

    def _check_cn_session_open(self) -> RiskGateResult:
        try:
            ctx = build_market_phase_context(market="cn", current_time=datetime.now().astimezone())
            phase = getattr(ctx, "phase", None)
            phase_value = phase.value if isinstance(phase, MarketPhase) else str(phase or "")
            if phase_value not in _LIVE_OPEN_PHASES:
                return RiskGateResult(
                    False,
                    "market_session_closed",
                    {"phase": phase_value},
                )
            return RiskGateResult(True, "ok")
        except Exception as exc:
            logger.warning("execution session check failed closed: %s", exc)
            return RiskGateResult(False, "market_session_unknown")


def _special_board_reason(stock_code: str) -> Optional[str]:
    digits = "".join(ch for ch in (stock_code or "") if ch.isdigit())
    if len(digits) >= 6:
        code6 = digits[-6:]
        if code6.startswith(("8", "4")):
            return "bse_unsupported"
    return None
