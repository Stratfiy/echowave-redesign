"""Grant (or revoke) staff access for an account.

The admin billing dashboard sits behind `is_superuser`, and nothing in the
normal signup flow ever sets that flag — so a fresh install has no way to reach
it. This is that way.

    python -m scripts.grant_superuser you@example.com
    python -m scripts.grant_superuser you@example.com --revoke
    python -m scripts.grant_superuser --list

Requires DATABASE_URL, like every other repo-owned script:

    set -a && source api/.env && set +a && python -m scripts.grant_superuser ...
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from api.db import db_client  # noqa: E402
from api.db.models import UserModel  # noqa: E402


async def list_users(session: AsyncSession) -> None:
    users = (
        await session.scalars(select(UserModel).order_by(UserModel.id))
    ).all()
    if not users:
        print("No users yet. Sign up through the UI first.")
        return

    print(f"{'id':>4}  {'staff':<6}  email")
    for user in users:
        print(
            f"{user.id:>4}  {'yes' if user.is_superuser else 'no':<6}  "
            f"{user.email or '(no email)'}"
        )


async def set_superuser(session: AsyncSession, email: str, *, grant: bool) -> int:
    user = await session.scalar(select(UserModel).where(UserModel.email == email))
    if user is None:
        print(f"No account with email {email!r}.")
        print("Sign up through the UI first, then re-run this. Known accounts:")
        await list_users(session)
        return 1

    if user.is_superuser == grant:
        print(
            f"{email} is already {'staff' if grant else 'not staff'}; nothing to do."
        )
        return 0

    user.is_superuser = grant
    await session.commit()

    if grant:
        print(f"{email} is now staff.")
        print("Open /superadmin/billing to reach the admin dashboard.")
    else:
        print(f"{email} is no longer staff.")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Grant or revoke admin dashboard access for an account."
    )
    parser.add_argument("email", nargs="?", help="Account email address.")
    parser.add_argument(
        "--revoke",
        action="store_true",
        help="Remove staff access instead of granting it.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_only",
        help="List accounts and who currently has staff access.",
    )
    args = parser.parse_args()

    if not args.list_only and not args.email:
        parser.error("an email is required unless --list is given")

    async with db_client.async_session() as session:
        if args.list_only:
            await list_users(session)
            return 0
        return await set_superuser(session, args.email, grant=not args.revoke)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
