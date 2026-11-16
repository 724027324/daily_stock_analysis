# -*- coding: utf-8 -*-
"""Persistence for execution order intents."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, func, select

from src.storage import DatabaseManager, ExecutionOrderIntentRecord, utc_naive_now


class ExecutionIntentRepository:
    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or DatabaseManager.get_instance()

    def create(self, fields: Dict[str, Any]) -> ExecutionOrderIntentRecord:
        payload = dict(fields)
        if "metadata_json" in payload and not isinstance(payload["metadata_json"], str):
            payload["metadata_json"] = json.dumps(payload["metadata_json"], ensure_ascii=False)
        with self.db.get_session() as session:
            row = ExecutionOrderIntentRecord(**payload)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def get(self, intent_id: int) -> Optional[ExecutionOrderIntentRecord]:
        with self.db.get_session() as session:
            return session.execute(
                select(ExecutionOrderIntentRecord)
                .where(ExecutionOrderIntentRecord.id == intent_id)
                .limit(1)
            ).scalar_one_or_none()

    def get_by_idempotency_key(self, key: str) -> Optional[ExecutionOrderIntentRecord]:
        with self.db.get_session() as session:
            return session.execute(
                select(ExecutionOrderIntentRecord)
                .where(ExecutionOrderIntentRecord.idempotency_key == key)
                .limit(1)
            ).scalar_one_or_none()

    def list(
        self,
        *,
        status: Optional[str] = None,
        stock_code: Optional[str] = None,
        signal_id: Optional[int] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[ExecutionOrderIntentRecord], int]:
        safe_page = max(1, int(page))
        safe_size = max(1, min(int(page_size), 100))
        conditions = []
        if status:
            conditions.append(ExecutionOrderIntentRecord.status == status)
        if stock_code:
            conditions.append(ExecutionOrderIntentRecord.stock_code == stock_code)
        if signal_id is not None:
            conditions.append(ExecutionOrderIntentRecord.signal_id == int(signal_id))

        with self.db.get_session() as session:
            count_stmt = select(func.count(ExecutionOrderIntentRecord.id))
            list_stmt = select(ExecutionOrderIntentRecord)
            if conditions:
                for cond in conditions:
                    count_stmt = count_stmt.where(cond)
                    list_stmt = list_stmt.where(cond)
            total = int(session.execute(count_stmt).scalar_one() or 0)
            rows = list(
                session.execute(
                    list_stmt.order_by(
                        desc(ExecutionOrderIntentRecord.created_at),
                        desc(ExecutionOrderIntentRecord.id),
                    )
                    .offset((safe_page - 1) * safe_size)
                    .limit(safe_size)
                ).scalars()
            )
            return rows, total

    def update_fields(self, intent_id: int, fields: Dict[str, Any]) -> Optional[ExecutionOrderIntentRecord]:
        payload = dict(fields)
        if "metadata_json" in payload and not isinstance(payload["metadata_json"], str):
            payload["metadata_json"] = json.dumps(payload["metadata_json"], ensure_ascii=False)
        payload["updated_at"] = utc_naive_now()
        with self.db.get_session() as session:
            row = session.execute(
                select(ExecutionOrderIntentRecord)
                .where(ExecutionOrderIntentRecord.id == intent_id)
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            for key, value in payload.items():
                setattr(row, key, value)
            session.commit()
            session.refresh(row)
            return row
