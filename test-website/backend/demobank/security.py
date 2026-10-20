from __future__ import annotations

import hashlib
import hmac


def hash_session_token(token: str, secret: str) -> str:
    """Return a keyed, irreversible representation of a session token."""
    return hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()
