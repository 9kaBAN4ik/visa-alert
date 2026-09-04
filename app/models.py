from __future__ import annotations

import hashlib

from pydantic import BaseModel


class Slot(BaseModel):
    """One bookable appointment slot."""

    center: str          # e.g. "Islamabad"
    category: str        # visa type / sub-category
    date: str            # ISO date
    time: str | None = None
    count: int | None = None
    book_url: str | None = None

    @property
    def key(self) -> str:
        """Stable identity used for de-duplication across polls."""
        raw = f"{self.center}|{self.category}|{self.date}|{self.time or ''}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def human(self) -> str:
        when = f"{self.date} {self.time}" if self.time else self.date
        tail = f" x{self.count}" if self.count else ""
        return f"**{self.center}** - {self.category} - `{when}`{tail}"
