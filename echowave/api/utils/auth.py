from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from api.constants import OSS_JWT_EXPIRY_HOURS, OSS_JWT_SECRET


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password, answering False rather than raising.

    bcrypt 5.x raises on anything over 72 bytes, and this is reached straight
    from the unauthenticated login route with a caller-controlled string — so
    without this, posting a long password turns a failed login into a 500.
    That is an availability bug and an oracle: a 500 and a 401 tell an attacker
    different things about the same endpoint.

    No password we will ever store can be over the limit (``SignupRequest``
    refuses them), so anything that long is by definition not the password.
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_jwt_token(user_id: int, email: str) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": datetime.now(UTC) + timedelta(hours=OSS_JWT_EXPIRY_HOURS),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, OSS_JWT_SECRET, algorithm="HS256")


def decode_jwt_token(token: str) -> dict:
    return jwt.decode(token, OSS_JWT_SECRET, algorithms=["HS256"])
