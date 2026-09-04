"""Discord delivery.

Deliberately defensive: a notifier that raises is worse than one that drops a
message, because the poll loop is the thing that must stay alive. Every failure
path here logs and returns rather than propagating.
"""

from __future__ import annotations

import logging
import time

import httpx

from .models import Slot

log = logging.getLogger(__name__)

# Discord allows 25 embed fields, but more than ~10 lines is unreadable on a
# phone - and a phone is where these alerts actually get seen.
MAX_LINES = 10


class Discord:
    def __init__(self, webhook_url: str, mention: str = "", dry_run: bool = False) -> None:
        self._url = webhook_url
        self._mention = mention
        self._dry_run = dry_run
        self._client = httpx.Client(timeout=10)

    def _post(self, payload: dict) -> bool:
        if self._dry_run or not self._url:
            log.info("[dry-run] discord <- %s", _preview(payload))
            return True

        for attempt in range(1, 4):
            try:
                r = self._client.post(self._url, json=payload)
                if r.status_code == 429:
                    wait = float(r.json().get("retry_after", 1.0))
                    log.warning("discord rate limited, sleeping %.1fs", wait)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return True
            except httpx.HTTPError as exc:
                log.warning("discord post failed (%d/3): %s", attempt, exc)
                time.sleep(2 * attempt)

        log.error("discord post gave up: %s", _preview(payload))
        return False

    def slots(self, slots: list[Slot]) -> bool:
        """Announce newly available slots as a single message."""
        if not slots:
            return True

        shown = slots[:MAX_LINES]
        lines = [s.human() for s in shown]
        if len(slots) > MAX_LINES:
            lines.append(f"_...and {len(slots) - MAX_LINES} more_")

        embed = {
            "title": f"{len(slots)} appointment slot(s) open",
            "description": "\n".join(lines),
            "color": 0x2ECC71,
            "timestamp": _now_iso(),
        }
        # Link straight to the booking page - the whole point is speed.
        book_url = next((s.book_url for s in shown if s.book_url), None)
        if book_url:
            embed["url"] = book_url

        return self._post(
            {
                "content": self._mention,
                "embeds": [embed],
                "allowed_mentions": {"parse": ["roles", "everyone"]},
            }
        )

    def notice(self, text: str, ok: bool = True) -> bool:
        """Operational message: heartbeat, recovery, fatal error. Never pings."""
        return self._post(
            {
                "embeds": [
                    {
                        "description": text,
                        "color": 0x3498DB if ok else 0xE74C3C,
                        "timestamp": _now_iso(),
                    }
                ],
                "allowed_mentions": {"parse": []},
            }
        )

    def close(self) -> None:
        self._client.close()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _preview(payload: dict) -> str:
    embeds = payload.get("embeds") or [{}]
    return (embeds[0].get("description") or payload.get("content") or "")[:200]
