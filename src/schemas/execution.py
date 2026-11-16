# -*- coding: utf-8 -*-
"""Execution-layer enums and helpers (independent from DecisionSignal)."""

from __future__ import annotations

from typing import Dict, FrozenSet, Literal, get_args

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
ExecutionSide = Literal["buy", "sell"]
ExecutionBroker = Literal["tdx", "paper"]

CONFIRM_MODES: FrozenSet[str] = frozenset(get_args(ExecutionConfirmMode))
INTENT_STATUSES: FrozenSet[str] = frozenset(get_args(ExecutionIntentStatus))
EXECUTION_SIDES: FrozenSet[str] = frozenset(get_args(ExecutionSide))
EXECUTION_BROKERS: FrozenSet[str] = frozenset(get_args(ExecutionBroker))

DEFAULT_ALLOWED_ACTIONS: FrozenSet[str] = frozenset({"buy", "add", "sell", "reduce"})

ACTION_TO_SIDE: Dict[str, ExecutionSide] = {
    "buy": "buy",
    "add": "buy",
    "sell": "sell",
    "reduce": "sell",
}

TERMINAL_STATUSES: FrozenSet[str] = frozenset(
    {"submitted", "rejected", "cancelled", "failed"}
)
CONFIRMABLE_STATUSES: FrozenSet[str] = frozenset({"pending_confirm"})
CANCELABLE_STATUSES: FrozenSet[str] = frozenset({"pending_confirm", "draft", "auto_submit"})
