"""FSM-хранилище aiogram в PostgreSQL. Переживает рестарты Railway и держит ПДн анкеты в РФ."""
from __future__ import annotations

from typing import Any, Mapping

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db.models import FsmState


def make_key(key: StorageKey) -> str:
    # В личке chat_id == user_id; delete_user чистит ключи по шаблону "%:<id>:<id>:%"
    return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.destiny}"


def _state_str(state: StateType) -> str | None:
    if state is None:
        return None
    return state.state if isinstance(state, State) else str(state)


class PgStorage(BaseStorage):
    def __init__(self, sm: async_sessionmaker):
        self.sm = sm

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = _state_str(state)
        async with self.sm() as s, s.begin():
            stmt = insert(FsmState).values(key=make_key(key), state=value, data={})
            await s.execute(stmt.on_conflict_do_update(index_elements=[FsmState.key], set_={"state": value}))

    async def get_state(self, key: StorageKey) -> str | None:
        async with self.sm() as s:
            return (await s.execute(select(FsmState.state).where(FsmState.key == make_key(key)))).scalar_one_or_none()

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        payload = dict(data)
        async with self.sm() as s, s.begin():
            if not payload:
                # пустые данные и пустое состояние: строку не держим
                row = await s.get(FsmState, make_key(key))
                if row is not None and row.state is None:
                    await s.execute(delete(FsmState).where(FsmState.key == row.key))
                    return
            stmt = insert(FsmState).values(key=make_key(key), state=None, data=payload)
            await s.execute(stmt.on_conflict_do_update(index_elements=[FsmState.key], set_={"data": payload}))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with self.sm() as s:
            data = (await s.execute(select(FsmState.data).where(FsmState.key == make_key(key)))).scalar_one_or_none()
        return dict(data or {})

    async def close(self) -> None:
        return None
