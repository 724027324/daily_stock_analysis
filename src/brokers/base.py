# -*- coding: utf-8 -*-
"""Broker execution port used by the execution orchestrator."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class BrokerOrderRequest:
    stock_code: str
    side: str
    quantity: int
    limit_price: float
    idempotency_key: str
    stock_name: Optional[str] = None


@dataclass(frozen=True)
class BrokerOrderResult:
    success: bool
    broker_order_id: Optional[str] = None
    message: str = ""
    raw: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class BrokerPosition:
    stock_code: str
    quantity: float
    available_quantity: float
    market: str = "cn"


@dataclass(frozen=True)
class BrokerCash:
    available: float
    currency: str = "CNY"


class BrokerExecutionPort(ABC):
    """Minimal execution surface for A-share adapters."""

    @abstractmethod
    def place_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> BrokerOrderResult:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> List[BrokerPosition]:
        raise NotImplementedError

    @abstractmethod
    def get_cash(self) -> BrokerCash:
        raise NotImplementedError
