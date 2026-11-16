# -*- coding: utf-8 -*-
"""In-process paper broker for dry-run / LIVE_TRADING_ENABLED=false."""

from __future__ import annotations

import itertools
import logging
import threading
from typing import Dict, List

from src.brokers.base import (
    BrokerCash,
    BrokerExecutionPort,
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerPosition,
)


logger = logging.getLogger(__name__)


class PaperBrokerAdapter(BrokerExecutionPort):
    """Records orders in memory and always succeeds."""

    def __init__(self, *, starting_cash: float = 1_000_000.0) -> None:
        self._lock = threading.Lock()
        self._cash = float(starting_cash)
        self._positions: Dict[str, float] = {}
        self._orders: Dict[str, BrokerOrderRequest] = {}
        self._id_seq = itertools.count(1)

    def place_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        with self._lock:
            order_id = f"paper-{next(self._id_seq)}"
            notional = float(request.quantity) * float(request.limit_price)
            if request.side == "buy":
                if notional > self._cash + 1e-9:
                    return BrokerOrderResult(
                        success=False,
                        message="paper_insufficient_cash",
                    )
                self._cash -= notional
                self._positions[request.stock_code] = (
                    self._positions.get(request.stock_code, 0.0) + float(request.quantity)
                )
            elif request.side == "sell":
                held = self._positions.get(request.stock_code, 0.0)
                if float(request.quantity) > held + 1e-9:
                    return BrokerOrderResult(
                        success=False,
                        message="paper_insufficient_position",
                    )
                self._positions[request.stock_code] = held - float(request.quantity)
                self._cash += notional
            else:
                return BrokerOrderResult(success=False, message=f"unsupported_side:{request.side}")

            self._orders[order_id] = request
            logger.info(
                "Paper broker accepted order id=%s code=%s side=%s qty=%s px=%s",
                order_id,
                request.stock_code,
                request.side,
                request.quantity,
                request.limit_price,
            )
            return BrokerOrderResult(
                success=True,
                broker_order_id=order_id,
                message="paper_submitted",
                raw={"notional": notional},
            )

    def cancel_order(self, broker_order_id: str) -> BrokerOrderResult:
        with self._lock:
            if broker_order_id not in self._orders:
                return BrokerOrderResult(success=False, message="paper_order_not_found")
            del self._orders[broker_order_id]
            return BrokerOrderResult(
                success=True,
                broker_order_id=broker_order_id,
                message="paper_cancelled",
            )

    def get_positions(self) -> List[BrokerPosition]:
        with self._lock:
            return [
                BrokerPosition(
                    stock_code=code,
                    quantity=qty,
                    available_quantity=qty,
                    market="cn",
                )
                for code, qty in self._positions.items()
                if qty > 0
            ]

    def get_cash(self) -> BrokerCash:
        with self._lock:
            return BrokerCash(available=self._cash, currency="CNY")
