from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    session_secret: str
    session_ttl_seconds: int = 24 * 60 * 60

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.getenv("DATABASE_URL", "").strip()
        session_secret = os.getenv("SESSION_SECRET", "")
        missing = [
            name
            for name, value in (
                ("DATABASE_URL", database_url),
                ("SESSION_SECRET", session_secret),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing required environment variable(s): {', '.join(missing)}")
        return cls(database_url=database_url, session_secret=session_secret)
