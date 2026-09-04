"""Persistent view of what we have already announced.

Two jobs:
  * do not ping the channel twice for the same slot;
  * forget a slot once it disappears, so that if it comes back later it is
    announced again (it is a genuinely new opportunity at that point).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .models import Slot

# A slot that vanished is only forgotten after this long, to ride out flapping
# in the upstream response.
FORGET_AFTER_SEC = 900


class SeenStore:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._seen: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._seen = json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError):
                self._seen = {}

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._seen))
        tmp.replace(self._path)

    def new_slots(self, slots: list[Slot]) -> list[Slot]:
        """Return the slots we have not announced yet, and mark them announced."""
        now = time.time()
        current = {s.key for s in slots}

        fresh = [s for s in slots if s.key not in self._seen]

        # refresh timestamps for everything still on offer
        for s in slots:
            self._seen[s.key] = now

        # drop entries that have been gone long enough
        self._seen = {
            k: ts
            for k, ts in self._seen.items()
            if k in current or now - ts < FORGET_AFTER_SEC
        }

        self._save()
        return fresh
