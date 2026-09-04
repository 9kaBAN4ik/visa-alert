"""Where slots come from.

Everything downstream depends only on `SlotSource.fetch() -> list[Slot]`, so the
access method can change without touching the loop, the dedup store or the
notifier. That matters here: the access story is the unsettled part of this
project and will likely be rewritten once the agency API question is answered.

Two implementations ship:

  HttpJsonSource - a plain, honest HTTP GET against a JSON endpoint, with
                   optional proxy routing and optional session headers/cookies
                   that you supply from an authorised session of your own.
                   No fingerprint spoofing, no challenge solving: if the target
                   fronts its API with a bot-protection WAF, this will be
                   refused, and that refusal is the site's answer.

  MockSource     - deterministic-ish fake data so the pipeline, the Discord
                   formatting and the dedup logic can be exercised end to end
                   before any real access exists.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import date, timedelta
from typing import Protocol

import httpx

from .models import Slot

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    """Fetch failed in a way the loop should back off from."""


class SlotSource(Protocol):
    def fetch(self) -> list[Slot]: ...
    def close(self) -> None: ...


# --------------------------------------------------------------------- http
class HttpJsonSource:
    def __init__(
        self,
        url: str,
        method: str = "GET",
        proxy: str | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._url = url
        self._method = method.upper()
        self._client = httpx.Client(
            proxy=proxy or None,
            timeout=timeout,
            follow_redirects=True,
            headers={"Accept": "application/json", **(headers or {})},
            cookies=cookies or {},
        )

    def fetch(self) -> list[Slot]:
        try:
            r = self._client.request(self._method, self._url)
        except httpx.HTTPError as exc:
            raise SourceError(f"request failed: {exc}") from exc

        if r.status_code in (401, 403):
            raise SourceError(
                f"HTTP {r.status_code} - the endpoint refused this client. "
                "Either the session is not authorised, or access is gated. "
                "Sort out legitimate access; do not try to disguise the client."
            )
        if r.status_code >= 400:
            raise SourceError(f"HTTP {r.status_code}")

        try:
            payload = r.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise SourceError(f"response was not JSON: {exc}") from exc

        return self.parse(payload)

    @staticmethod
    def parse(payload: object) -> list[Slot]:
        """Map the endpoint's response onto `Slot`.

        Left generic on purpose: the real shape is unknown until someone with
        access captures a HAR of the date-selection page. Adjust the field names
        below to match, and everything else in the project keeps working.
        """
        rows: list[dict] = []
        if isinstance(payload, list):
            rows = [r for r in payload if isinstance(r, dict)]
        elif isinstance(payload, dict):
            for key in ("data", "slots", "results", "items", "appointments"):
                value = payload.get(key)
                if isinstance(value, list):
                    rows = [r for r in value if isinstance(r, dict)]
                    break

        slots: list[Slot] = []
        for row in rows:
            available = row.get("available", row.get("is_available", True))
            count = _int_or_none(row.get("count", row.get("available_slots")))
            if available is False or count == 0:
                continue

            slot_date = row.get("date") or row.get("appointment_date")
            if not slot_date:
                continue

            slots.append(
                Slot(
                    center=str(row.get("center") or row.get("location") or "unknown"),
                    category=str(row.get("category") or row.get("visa_type") or "any"),
                    date=str(slot_date),
                    time=_str_or_none(row.get("time") or row.get("slot_time")),
                    count=count,
                    book_url=_str_or_none(row.get("url") or row.get("booking_url")),
                )
            )
        return slots

    def close(self) -> None:
        self._client.close()


# --------------------------------------------------------------------- mock
class MockSource:
    """Fake source for wiring up and demoing the pipeline.

    Emits nothing most of the time, then a burst - which is roughly how the real
    thing behaves, and exercises the dedup path properly.
    """

    CENTERS = ("Islamabad", "Karachi", "Lahore")
    CATEGORIES = ("Schengen - Tourist", "Schengen - Business")

    def __init__(self, hit_chance: float = 0.35, seed: int | None = None) -> None:
        self._hit_chance = hit_chance
        self._rng = random.Random(seed)

    def fetch(self) -> list[Slot]:
        if self._rng.random() > self._hit_chance:
            return []

        out = []
        for _ in range(self._rng.randint(1, 3)):
            day = date.today() + timedelta(days=self._rng.randint(3, 45))
            out.append(
                Slot(
                    center=self._rng.choice(self.CENTERS),
                    category=self._rng.choice(self.CATEGORIES),
                    date=day.isoformat(),
                    time=f"{self._rng.randint(9, 16):02d}:{self._rng.choice(('00', '30'))}",
                    count=self._rng.randint(1, 4),
                    book_url="https://example.invalid/book",
                )
            )
        return out

    def close(self) -> None:
        pass


def _int_or_none(v: object) -> int | None:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _str_or_none(v: object) -> str | None:
    return str(v) if v not in (None, "") else None
