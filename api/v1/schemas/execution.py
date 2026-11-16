# -*- coding: utf-8 -*-
"""API schemas for the execution order intent layer."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


ExecutionConfirmMode = Literal["manual", "auto"]
ExecutionIntentStatus = Literal[
    "draft",
    "pending_confirm",
    "auto_submit",
    "submitted",
    "rejected",
    "cancelled",
    "failed",
]


class ExecutionIntentItem(BaseModel):
    id: int
    signal_id: int
    stock_code: str
    stock_name: Optional[str] = None
    market: str
    action: str
    side: str
    quantity: int
    limit_price: float
    notional_cny: Optional[float] = None
    status: ExecutionIntentStatus
    confirm_mode: ExecutionConfirmMode
    broker: str
    idempotency_key: str
    reject_reason: Optional[str] = None
    broker_order_id: Optional[str] = None
    portfolio_account_id: Optional[int] = None
    live_enabled: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    submitted_at: Optional[str] = None


class ExecutionIntentListResponse(BaseModel):
    items: List[ExecutionIntentItem]
    total: int
    page: int
    page_size: int


class ExecutionIntentActionRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=256)
