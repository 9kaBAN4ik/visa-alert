from __future__ import annotations

import json

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- target ---------------------------------------------------------
    base_url: str = ""
    slots_path: str = ""
    slots_method: str = "GET"

    # --- access ---------------------------------------------------------
    proxy_url: str | None = None
    # Headers/cookies from a session you are authorised to use, as JSON objects.
    # e.g. SESSION_COOKIES={"sessionid": "..."}
    session_headers: str = "{}"
    session_cookies: str = "{}"

    # --- polling --------------------------------------------------------
    # 6s is already aggressive. Going lower buys little and looks like abuse to
    # any rate limiter worth the name.
    poll_interval_sec: float = 6.0
    poll_jitter_sec: float = 2.0
    max_backoff_sec: float = 300.0
    max_consecutive_errors: int = 20

    # --- discord --------------------------------------------------------
    discord_webhook_url: str = ""
    discord_mention: str = ""
    discord_heartbeat_webhook_url: str | None = None

    # --- ops ------------------------------------------------------------
    heartbeat_every_sec: float = 1800.0
    state_file: str = "state.json"
    log_level: str = "INFO"

    @property
    def slots_url(self) -> str:
        return self.base_url.rstrip("/") + "/" + self.slots_path.lstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        return _json_obj(self.session_headers, "SESSION_HEADERS")

    @property
    def cookies(self) -> dict[str, str]:
        return _json_obj(self.session_cookies, "SESSION_COOKIES")

    def check_live(self) -> list[str]:
        """Problems that stop a real (non-mock) run. Empty list means good."""
        problems = []
        if not self.base_url or not self.slots_path:
            problems.append("BASE_URL and SLOTS_PATH must be set")
        if not self.discord_webhook_url:
            problems.append("DISCORD_WEBHOOK_URL must be set")
        return problems


def _json_obj(raw: str, name: str) -> dict[str, str]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object")
    return {str(k): str(v) for k, v in parsed.items()}


settings = Settings()
