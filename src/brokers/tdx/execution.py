# -*- coding: utf-8 -*-
"""Tongdaxin Windows client automation via optional easytrader dependency."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, List, Optional

from src.brokers.base import (
    BrokerCash,
    BrokerExecutionPort,
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerPosition,
)


logger = logging.getLogger(__name__)


class TongdaxinExecutionError(RuntimeError):
    """Raised when the Tongdaxin adapter cannot operate safely."""


class TongdaxinExecutionAdapter(BrokerExecutionPort):
    """Place orders through a locally logged-in Tongdaxin trading client.

    Requires Windows + optional ``easytrader``. Trade unlock password must come
    from the local process environment (``TDX_TRADE_PASSWORD``), never from chat.
    """

    def __init__(
        self,
        *,
        client_path: Optional[str] = None,
        account_id: Optional[str] = None,
        trade_password: Optional[str] = None,
    ) -> None:
        if sys.platform != "win32":
            raise TongdaxinExecutionError(
                "Tongdaxin execution adapter requires Windows (win32)."
            )
        self.client_path = (client_path or os.getenv("TDX_CLIENT_PATH") or "").strip()
        self.account_id = (account_id or os.getenv("TDX_ACCOUNT_ID") or "").strip()
        self._trade_password = (
            trade_password
            if trade_password is not None
            else (os.getenv("TDX_TRADE_PASSWORD") or "")
        ).strip()
        if not self.client_path:
            raise TongdaxinExecutionError("TDX_CLIENT_PATH is required for live tdx orders")
        self._user: Any = None

    def _ensure_client(self) -> Any:
        if self._user is not None:
            return self._user
        try:
            import easytrader  # type: ignore
        except ImportError as exc:
            raise TongdaxinExecutionError(
                "easytrader is not installed; install it locally to enable Tongdaxin trading"
            ) from exc
        try:
            user = easytrader.use("universal_client")
            user.connect(self.client_path)
            if self._trade_password:
                user.enable_type_keys_for_editor()
                try:
                    user.prepare(password=self._trade_password)
                except Exception:
                    # Some clients unlock via connect alone; keep going if prepare fails softly.
                    logger.warning("Tongdaxin prepare/unlock raised; continuing with connected client")
            self._user = user
            return user
        except TongdaxinExecutionError:
            raise
        except Exception as exc:
            raise TongdaxinExecutionError(f"failed to connect Tongdaxin client: {exc}") from exc

    def place_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        user = self._ensure_client()
        code = _normalize_tdx_code(request.stock_code)
        try:
            if request.side == "buy":
                raw = user.buy(code, price=float(request.limit_price), amount=int(request.quantity))
            elif request.side == "sell":
                raw = user.sell(code, price=float(request.limit_price), amount=int(request.quantity))
            else:
                return BrokerOrderResult(success=False, message=f"unsupported_side:{request.side}")
            order_id = _extract_order_id(raw) or request.idempotency_key
            return BrokerOrderResult(
                success=True,
                broker_order_id=str(order_id),
                message="tdx_submitted",
                raw=_as_dict(raw),
            )
        except Exception as exc:
            logger.error("Tongdaxin place_order failed: %s", exc, exc_info=True)
            return BrokerOrderResult(success=False, message=f"tdx_place_failed:{exc}")

    def cancel_order(self, broker_order_id: str) -> BrokerOrderResult:
        user = self._ensure_client()
        try:
            raw = user.cancel_entrust(broker_order_id)
            return BrokerOrderResult(
                success=True,
                broker_order_id=broker_order_id,
                message="tdx_cancelled",
                raw=_as_dict(raw),
            )
        except Exception as exc:
            logger.error("Tongdaxin cancel_order failed: %s", exc, exc_info=True)
            return BrokerOrderResult(success=False, message=f"tdx_cancel_failed:{exc}")

    def get_positions(self) -> List[BrokerPosition]:
        user = self._ensure_client()
        try:
            raw_positions = user.position or []
        except Exception as exc:
            raise TongdaxinExecutionError(f"failed to read Tongdaxin positions: {exc}") from exc
        out: List[BrokerPosition] = []
        for row in raw_positions:
            if not isinstance(row, dict):
                continue
            code = str(
                row.get("证券代码")
                or row.get("stock_code")
                or row.get("code")
                or ""
            ).strip()
            if not code:
                continue
            qty = _to_float(row.get("股票余额") or row.get("当前持仓") or row.get("volume") or 0)
            avail = _to_float(
                row.get("可用余额") or row.get("可用股份") or row.get("enable_amount") or qty
            )
            out.append(
                BrokerPosition(
                    stock_code=code.zfill(6) if code.isdigit() else code,
                    quantity=qty,
                    available_quantity=avail,
                    market="cn",
                )
            )
        return out

    def get_cash(self) -> BrokerCash:
        user = self._ensure_client()
        try:
            balance = user.balance or {}
        except Exception as exc:
            raise TongdaxinExecutionError(f"failed to read Tongdaxin cash: {exc}") from exc
        if isinstance(balance, list) and balance:
            balance = balance[0] if isinstance(balance[0], dict) else {}
        if not isinstance(balance, dict):
            balance = {}
        available = _to_float(
            balance.get("可用金额")
            or balance.get("可用资金")
            or balance.get("enable_balance")
            or balance.get("资金余额")
            or 0
        )
        return BrokerCash(available=available, currency="CNY")


def _normalize_tdx_code(stock_code: str) -> str:
    code = (stock_code or "").strip().upper()
    for suffix in (".SH", ".SZ", ".SS", ".BJ"):
        if code.endswith(suffix):
            code = code[: -len(suffix)]
            break
    if code.startswith(("SH", "SZ", "BJ")) and len(code) > 2:
        code = code[2:]
    digits = "".join(ch for ch in code if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:]
    return code


def _extract_order_id(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        for key in ("entrust_no", "order_id", "合同编号", "委托编号"):
            if raw.get(key) is not None:
                return str(raw.get(key))
    return str(raw) if raw else None


def _as_dict(raw: Any) -> Optional[dict]:
    return dict(raw) if isinstance(raw, dict) else None


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
