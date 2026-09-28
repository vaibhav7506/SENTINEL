"""Every browser-facing transaction has application filters and a non-owner RLS role."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, with_loader_criteria

from app.saas.models import Account, AuthSession, TenantOwned


class TenantSession(Session):
    pass


@event.listens_for(TenantSession, "after_begin")
def set_database_context(session: Session, transaction: Any, connection: Any) -> None:
    account_id = session.info["account_id"]
    connection.execute(text("SET LOCAL ROLE sentinel_app"))
    connection.execute(
        text("SELECT set_config('sentinel.account_id',:account,true)"),
        {"account": str(account_id)},
    )


@event.listens_for(TenantSession, "do_orm_execute")
def filter_account(state: Any) -> None:
    account_id = state.session.info["account_id"]
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantOwned, lambda cls: cls.account_id == account_id, include_aliases=True
        ),
        with_loader_criteria(
            AuthSession, lambda cls: cls.account_id == account_id, include_aliases=True
        ),
        with_loader_criteria(Account, lambda cls: cls.id == account_id, include_aliases=True),
    )


@asynccontextmanager
async def account_session(engine: Any, account_id: UUID) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(engine, expire_on_commit=False, sync_session_class=TenantSession)
    async with factory(info={"account_id": account_id}) as session:
        yield session


@asynccontextmanager
async def request_session(request: Request) -> AsyncIterator[AsyncSession]:
    if not request.app.state.settings.saas_enabled:
        async with async_sessionmaker(
            request.app.state.engine, expire_on_commit=False
        )() as session:
            yield session
        return
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(401, "Authentication required")
    async with account_session(request.app.state.engine, principal.account_id) as session:
        yield session


async def get_for_account(session: AsyncSession, model: Any, identifier: Any) -> Any:
    """Use scoped SQL even for primary-key lookup; never rely on an identity-map shortcut."""
    return await session.scalar(select(model).where(model.id == identifier))
