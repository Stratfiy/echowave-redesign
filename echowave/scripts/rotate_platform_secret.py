"""Rewrite every stored secret under the current ``PLATFORM_CREDENTIAL_SECRET``.

Run this in the middle of a rotation, once both keys are configured. It is
re-runnable and safe to run while the platform is serving traffic.

    # 1. Generate the new key.
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

    # 2. In the environment: move the old value to PLATFORM_CREDENTIAL_SECRET_PREVIOUS,
    #    put the new one in PLATFORM_CREDENTIAL_SECRET. Restart the API and workers.

    # 3. See what is outstanding, without changing anything:
    python -m scripts.rotate_platform_secret --dry-run

    # 4. Do it:
    python -m scripts.rotate_platform_secret

    # 5. When "still on the previous key" reads 0, remove
    #    PLATFORM_CREDENTIAL_SECRET_PREVIOUS and restart again. Until that
    #    happens the old key still opens everything, so the rotation has not
    #    achieved anything yet.

The same thing is available from the staff provider-keys screen, which is the
better route on a deployment where nobody has a shell.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from api.db import db_client
from api.services import secret_rotation


async def main(dry_run: bool) -> int:
    try:
        secret_rotation.cipher()
    except secret_rotation.SecretError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not secret_rotation.rotation_in_progress():
        # Not an error — re-encrypting under the same single key is a no-op
        # that still reports honestly. But say so, because an operator who
        # forgot to set the previous key would otherwise read "0 rewritten" as
        # "already done" when it means "nothing could be read".
        print(
            "note: PLATFORM_CREDENTIAL_SECRET_PREVIOUS is not set, so anything "
            "written under an older key cannot be read and will be reported as "
            "unreadable rather than rewritten.\n"
        )

    async with db_client.async_session() as session:
        result = await secret_rotation.reencrypt_all(session, dry_run=dry_run)
        if not dry_run:
            await session.commit()
        state = await secret_rotation.status(session)

    print(f"{'Would rewrite' if dry_run else 'Rewritten'}: {result.rewritten}")
    for kind, count in sorted(result.by_kind.items()):
        print(f"  {kind}: {count}")
    print(f"Already on the current key: {result.skipped}")
    if result.unreadable:
        print(
            f"Unreadable and left untouched: {result.unreadable} — these were "
            "encrypted under a secret that is no longer configured."
        )

    print(f"\nStill on the previous key: {state.pending}")
    if state.pending == 0 and state.previous_key_configured:
        print(
            "Nothing is left on the previous key. Remove "
            "PLATFORM_CREDENTIAL_SECRET_PREVIOUS and restart to finish the "
            "rotation."
        )
    # A non-zero pending count after a real run means something could not be
    # rewritten, which the operator has to see in the exit code as well as the
    # output — this may well be running unattended.
    return 1 if (state.pending and not dry_run) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing anything.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.dry_run)))
