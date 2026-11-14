"""Thin async data-access helpers over the generated ORM models."""

from typing import Any, Type

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import text as sa_text

from core.database import db_manager


def session():
    return db_manager.session()


def row_to_dict(row: Any) -> dict:
    out = {}
    for col in row.__table__.columns:
        value = getattr(row, col.name)
        out[col.name] = value.isoformat() if hasattr(value, "isoformat") else value
    return out


async def add(orm_cls: Type, /, **fields: Any) -> dict:
    async with session() as s:
        row = orm_cls(**fields)
        s.add(row)
        await s.commit()
        return row_to_dict(row)


async def get(model: Type, item_id: int) -> dict | None:
    async with session() as s:
        row = await s.get(model, item_id)
        return row_to_dict(row) if row else None


async def find(model: Type, *where: Any, order: Any = None, limit: int = 200, offset: int = 0) -> list[dict]:
    async with session() as s:
        stmt = select(model).where(*where)
        if order is not None:
            stmt = stmt.order_by(order)
        rows = (await s.execute(stmt.offset(offset).limit(limit))).scalars().all()
        return [row_to_dict(r) for r in rows]


async def count(model: Type, *where: Any) -> int:
    from sqlalchemy import func

    async with session() as s:
        return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


async def update(model: Type, item_id: int, **fields: Any) -> dict | None:
    async with session() as s:
        row = await s.get(model, item_id)
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        await s.commit()
        return row_to_dict(row)


async def remove(model: Type, *where: Any) -> int:
    async with session() as s:
        result = await s.execute(sa_delete(model).where(*where))
        await s.commit()
        return result.rowcount or 0


async def append_events_json(run_key: str, payload_json: str) -> None:
    """Atomically append a JSON array to AgentRuns.events_json via a single UPDATE.

    Uses PostgreSQL jsonb concatenation so concurrent emitters (e.g. race lanes)
    can't lose events to a read-modify-write race. ``payload_json`` must be a JSON
    array, e.g. ``[{"type": "verify", "data": {...}}]``.
    """
    async with session() as s:
        await s.execute(
            sa_text(
                "UPDATE agent_runs "
                "SET events_json = (COALESCE(events_json, '[]')::jsonb || CAST(:payload AS jsonb))::text "
                "WHERE run_key = :run_key"
            ),
            {"payload": payload_json, "run_key": run_key},
        )
        await s.commit()
