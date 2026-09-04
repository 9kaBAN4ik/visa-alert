"""Poll loop.

Design rules, in priority order:
  1. Never die silently. A watcher nobody knows is dead is worse than no watcher.
  2. Never double-ping. Alert fatigue kills the channel within a day.
  3. Back off when the far end is unhappy, and say so out loud.
"""

from __future__ import annotations

import argparse
import logging
import random
import signal
import sys
import time

from .config import settings
from .models import Slot
from .notify import Discord
from .source import HttpJsonSource, MockSource, SlotSource, SourceError
from .state import SeenStore

log = logging.getLogger("watcher")

_stop = False


def _handle_signal(signum, _frame) -> None:
    global _stop
    log.info("signal %s received - finishing current cycle", signum)
    _stop = True


class Watcher:
    def __init__(self, source: SlotSource, discord: Discord, store: SeenStore) -> None:
        self._source = source
        self._discord = discord
        self._store = store
        self._errors = 0
        self._last_heartbeat = time.monotonic()
        self._degraded = False

    def cycle(self) -> list[Slot]:
        """One poll. Returns the slots that were newly announced."""
        try:
            slots = self._source.fetch()
        except SourceError as exc:
            self._on_error(exc)
            return []

        if self._degraded:
            self._discord.notice("Recovered - watching again.", ok=True)
            self._degraded = False
        self._errors = 0

        fresh = self._store.new_slots(slots)
        if fresh:
            log.info("%d new slot(s) of %d visible", len(fresh), len(slots))
            self._discord.slots(fresh)
        else:
            log.debug("%d slot(s) visible, nothing new", len(slots))
        return fresh

    def _on_error(self, exc: Exception) -> None:
        self._errors += 1
        log.warning("fetch failed (%d in a row): %s", self._errors, exc)

        # Announce once when things go bad, not on every failed poll.
        if self._errors == 3 and not self._degraded:
            self._degraded = True
            self._discord.notice(f"Fetch failing: `{exc}`", ok=False)

    def sleep(self) -> None:
        """Jittered interval, widened exponentially while errors persist."""
        base = settings.poll_interval_sec
        if self._errors:
            base = min(base * (2 ** min(self._errors, 8)), settings.max_backoff_sec)
        delay = max(0.5, base + random.uniform(-1, 1) * settings.poll_jitter_sec)

        # Wake early on shutdown rather than sitting out a long backoff.
        deadline = time.monotonic() + delay
        while not _stop and time.monotonic() < deadline:
            time.sleep(min(0.5, deadline - time.monotonic()))

    def maybe_heartbeat(self) -> None:
        if settings.heartbeat_every_sec <= 0:
            return
        if time.monotonic() - self._last_heartbeat < settings.heartbeat_every_sec:
            return
        self._last_heartbeat = time.monotonic()
        state = "degraded" if self._degraded else "ok"
        log.info("heartbeat (%s)", state)
        if settings.discord_heartbeat_webhook_url:
            Discord(settings.discord_heartbeat_webhook_url).notice(
                f"Still alive - status: **{state}**", ok=not self._degraded
            )

    def exhausted(self) -> bool:
        return self._errors >= settings.max_consecutive_errors

    def run(self) -> int:
        log.info("watching every ~%.0fs", settings.poll_interval_sec)
        while not _stop:
            self.cycle()
            self.maybe_heartbeat()
            if self.exhausted():
                msg = f"Giving up after {self._errors} consecutive failures."
                log.error(msg)
                self._discord.notice(msg, ok=False)
                return 1
            self.sleep()
        log.info("stopped cleanly")
        return 0


def build_source(args) -> SlotSource:
    if args.mock:
        return MockSource(seed=args.seed)
    return HttpJsonSource(
        url=settings.slots_url,
        method=settings.slots_method,
        proxy=settings.proxy_url,
        headers=settings.headers,
        cookies=settings.cookies,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="visa-alert")
    parser.add_argument("--once", action="store_true", help="single poll, then exit")
    parser.add_argument("--mock", action="store_true", help="fake source, no network")
    parser.add_argument("--dry-run", action="store_true", help="log alerts, do not post")
    parser.add_argument("--seed", type=int, default=None, help="seed for --mock")
    parser.add_argument("--state", default=None, help="override state file path")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if not args.mock:
        problems = settings.check_live()
        if problems:
            for p in problems:
                log.error("config: %s", p)
            log.error("copy .env.example to .env and fill it in, or use --mock")
            return 2

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    source = build_source(args)
    discord = Discord(
        settings.discord_webhook_url,
        settings.discord_mention,
        dry_run=args.dry_run or args.mock,
    )
    store = SeenStore(args.state or settings.state_file)
    watcher = Watcher(source, discord, store)

    try:
        if args.once:
            watcher.cycle()
            return 0
        return watcher.run()
    finally:
        source.close()
        discord.close()


if __name__ == "__main__":
    sys.exit(main())
