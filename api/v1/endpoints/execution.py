# -*- coding: utf-8 -*-
"""Execution order intent API (confirm / reject / cancel)."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Security
from fastapi.security import APIKeyCookie

from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.execution import (
    ExecutionIntentActionRequest,
    ExecutionIntentItem,
    ExecutionIntentListResponse,
)
from src.auth import COOKIE_NAME
from src.services.execution_orchestrator import (
    ExecutionOrchestrator,
    ExecutionOrchestratorError,
)


logger = logging.getLogger(__name__)

admin_session_cookie = APIKeyCookie(
    name=COOKIE_NAME,
    scheme_name="AdminSessionCookie",
    auto_error=False,
)
router = APIRouter(dependencies=[Security(admin_session_cookie)])

AUTH_RESPONSE = {
    401: {
        "model": ErrorResponse,
        "description": "未登录或管理员会话无效（ADMIN_AUTH_ENABLED=true 时）",
    },
}


def _bad_request(exc: Exception, *, error: str = "validation_error") -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": error, "message": str(exc)},
    )


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": "not_found", "message": str(exc)},
    )


def _internal_error(message: str, exc: Exception) -> HTTPException:
    logger.error("%s: %s", message, exc, exc_info=True)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": message},
    )


@router.get(
    "",
    response_model=ExecutionIntentListResponse,
    responses={**AUTH_RESPONSE},
    summary="List execution order intents",
    operation_id="listExecutionIntents",
)
def list_execution_intents(
    status: Optional[str] = Query(None),
    stock_code: Optional[str] = Query(None),
    signal_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> ExecutionIntentListResponse:
    service = ExecutionOrchestrator()
    try:
        data = service.list_intents(
            status=status,
            stock_code=stock_code,
            signal_id=signal_id,
            page=page,
            page_size=page_size,
        )
        return ExecutionIntentListResponse(**data)
    except Exception as exc:
        raise _internal_error("List execution intents failed", exc) from exc


@router.get(
    "/{intent_id}",
    response_model=ExecutionIntentItem,
    responses={
        **AUTH_RESPONSE,
        404: {"model": ErrorResponse, "description": "意图不存在"},
    },
    summary="Get one execution order intent",
    operation_id="getExecutionIntent",
)
def get_execution_intent(intent_id: int) -> ExecutionIntentItem:
    service = ExecutionOrchestrator()
    try:
        return ExecutionIntentItem(**service.get_intent(intent_id))
    except ExecutionOrchestratorError as exc:
        if "not_found" in str(exc):
            raise _not_found(exc) from exc
        raise _bad_request(exc) from exc
    except Exception as exc:
        raise _internal_error("Get execution intent failed", exc) from exc


@router.post(
    "/{intent_id}/confirm",
    response_model=ExecutionIntentItem,
    responses={
        **AUTH_RESPONSE,
        400: {"model": ErrorResponse, "description": "状态不允许确认"},
        404: {"model": ErrorResponse, "description": "意图不存在"},
    },
    summary="Confirm a pending execution intent",
    operation_id="confirmExecutionIntent",
)
def confirm_execution_intent(intent_id: int) -> ExecutionIntentItem:
    service = ExecutionOrchestrator()
    try:
        return ExecutionIntentItem(**service.confirm_intent(intent_id))
    except ExecutionOrchestratorError as exc:
        message = str(exc)
        if "not_found" in message:
            raise _not_found(exc) from exc
        raise _bad_request(exc, error="intent_state_error") from exc
    except Exception as exc:
        raise _internal_error("Confirm execution intent failed", exc) from exc


@router.post(
    "/{intent_id}/reject",
    response_model=ExecutionIntentItem,
    responses={
        **AUTH_RESPONSE,
        400: {"model": ErrorResponse, "description": "状态不允许拒绝"},
        404: {"model": ErrorResponse, "description": "意图不存在"},
    },
    summary="Reject a pending execution intent",
    operation_id="rejectExecutionIntent",
)
def reject_execution_intent(
    intent_id: int,
    body: Optional[ExecutionIntentActionRequest] = None,
) -> ExecutionIntentItem:
    service = ExecutionOrchestrator()
    reason = (body.reason if body else None) or "user_rejected"
    try:
        return ExecutionIntentItem(**service.reject_intent(intent_id, reason=reason))
    except ExecutionOrchestratorError as exc:
        message = str(exc)
        if "not_found" in message:
            raise _not_found(exc) from exc
        raise _bad_request(exc, error="intent_state_error") from exc
    except Exception as exc:
        raise _internal_error("Reject execution intent failed", exc) from exc


@router.post(
    "/{intent_id}/cancel",
    response_model=ExecutionIntentItem,
    responses={
        **AUTH_RESPONSE,
        400: {"model": ErrorResponse, "description": "状态不允许取消"},
        404: {"model": ErrorResponse, "description": "意图不存在"},
    },
    summary="Cancel a pending or auto-submit execution intent",
    operation_id="cancelExecutionIntent",
)
def cancel_execution_intent(
    intent_id: int,
    body: Optional[ExecutionIntentActionRequest] = None,
) -> ExecutionIntentItem:
    service = ExecutionOrchestrator()
    reason = (body.reason if body else None) or "user_cancelled"
    try:
        return ExecutionIntentItem(**service.cancel_intent(intent_id, reason=reason))
    except ExecutionOrchestratorError as exc:
        message = str(exc)
        if "not_found" in message:
            raise _not_found(exc) from exc
        raise _bad_request(exc, error="intent_state_error") from exc
    except Exception as exc:
        raise _internal_error("Cancel execution intent failed", exc) from exc
