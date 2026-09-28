"""Explicitly create the first owner of the migrated demo account; no default password."""

import argparse
import asyncio
import getpass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.event_loop import create_event_loop
from app.db.session import create_database_engine
from app.saas.auth import audit
from app.saas.models import DEMO_ACCOUNT_ID, Account, User
from app.saas.security import normalized_email, password_hash


async def bootstrap(settings: Settings, email: str, password: str) -> None:
    email = normalized_email(email)
    if not 12 <= len(password) <= 128:
        raise ValueError("Use a password between 12 and 128 characters")
    encoded = await asyncio.to_thread(password_hash, password)
    engine = create_database_engine(settings)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            account = await session.scalar(
                select(Account).where(Account.id == DEMO_ACCOUNT_ID).with_for_update()
            )
            if account is None:
                raise ValueError("Run the SaaS migration first")
            existing = await session.scalar(select(User.id).where(User.account_id == account.id))
            if existing is not None:
                raise ValueError("This account already has a user; use owner team management")
            user = User(account_id=account.id, email=email, password_hash=encoded, role="OWNER")
            session.add(user)
            await session.flush()
            audit(session, account.id, "user.created", user.id, user.id)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    password = getpass.getpass("New demo owner password (12+ characters): ")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords did not match")
    try:
        asyncio.run(bootstrap(Settings(), args.email, password), loop_factory=create_event_loop)
    except Exception:
        raise SystemExit("Owner bootstrap failed; verify migration and unique email") from None
    print("Demo owner created. Sign in through the browser.")


if __name__ == "__main__":
    main()
