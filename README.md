# visa-alert

Watches an appointment endpoint and posts newly opened slots to Discord.

Runnable today against a mock source. The only piece not wired up is where real
data comes from — see [Access](#access), which is the actual open question on
this project.

## Run it

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m app.main --mock --once
```

`--mock` needs no network and no config. To see the loop, dedup and heartbeat
working together:

```bash
POLL_INTERVAL_SEC=2 HEARTBEAT_EVERY_SEC=5 LOG_LEVEL=DEBUG .venv/Scripts/python.exe -m app.main --mock
```

For a real run, copy `.env.example` to `.env`, fill it in, and drop `--mock`.
Add `--dry-run` to log alerts instead of posting them.

| Flag | Effect |
|---|---|
| `--once` | one poll, then exit — good for cron and for smoke tests |
| `--mock` | fake source, no network, never posts |
| `--dry-run` | log what would be posted, post nothing |
| `--seed N` | make `--mock` reproducible |
| `--state P` | override the dedup state file |

## Layout

| File | Responsibility |
|---|---|
| `app/models.py` | `Slot`, plus the `key` used for dedup |
| `app/source.py` | where slots come from — `HttpJsonSource`, `MockSource` |
| `app/state.py` | what has already been announced |
| `app/notify.py` | Discord webhook client |
| `app/main.py` | poll loop, backoff, heartbeat, CLI |
| `app/config.py` | env-driven settings |

Everything downstream depends only on `SlotSource.fetch() -> list[Slot]`, so
changing how data is obtained touches one file.

## Behaviour worth knowing

**Dedup.** A slot is identified by center + category + date + time. It is
announced once. If it disappears and comes back after `FORGET_AFTER_SEC`
(15 min, in `state.py`), it is announced again — by then it is a genuinely new
opportunity. State survives restarts via `state.json`.

**Backoff.** Failures widen the interval exponentially up to `MAX_BACKOFF_SEC`.
After 3 consecutive failures the channel gets one "fetch failing" notice — not
one per poll — and one "recovered" notice when it comes back.

**Liveness.** A heartbeat goes to a separate webhook every 30 minutes. Point it
at a channel nobody watches, and alarm on its *absence*. A watcher that dies
quietly is worse than no watcher, and that is the failure mode you will actually
hit.

**Interval.** 6s default. Lower buys almost nothing — the notification path
itself costs a second or two — and looks like abuse to any rate limiter.

## Access

`HttpJsonSource` is a plain HTTP client. It sends a normal request and reads
JSON. It does not disguise itself, and it will not get past a bot-protection
WAF — the target of this project (`pk-gr-services.gvcworld.eu`) sits behind
Imperva and is region-limited, so an unauthorised client gets `403`.

That is the site's answer, and the fix is access, not disguise:

1. **Agency access.** GVCWorld is the visa-centre operator, not the consulate.
   Operators like this normally run a partner programme for travel agencies,
   with a documented API and a contract. This is the only route where the
   integration does not break every time the site updates.
2. **A documented public API**, if one exists. Settle this with a HAR capture:
   open the site, F12 → Network → navigate to the date-selection page →
   *Save all as HAR with content*. That answers in one go whether there is a
   JSON endpoint, whether it needs auth, and what the payload looks like.
3. **Your own authorised session.** If you have an account you are entitled to
   use, put its cookies in `SESSION_COOKIES` and it will poll as you.

Once the payload shape is known, adjust `HttpJsonSource.parse()` to match the
real field names. It currently guesses at common ones (`data`/`slots`/`results`
containers; `center`/`location`, `visa_type`/`category`, `date`, `time`, `count`)
and drops rows marked unavailable or zero-count.

## Deploying

Run under something that restarts it — systemd with `Restart=always`, or Docker
with `restart: unless-stopped`. Ship `state.json` on a persistent volume so a
restart does not re-announce everything. Watch for the heartbeat's absence.
