"""
Centralized configuration. Everything that varies between dev/staging/prod
(or that is a secret) lives in environment variables -- never hard-coded.
See .env.example for the full list.
"""
from functools import lru_cache
from typing import Any
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Database ---------------------------------------------------------
    database_url: str = "postgresql+asyncpg://fmda:fmda@localhost:5432/fmda"

    # --- Redis (circuit breaker state, rate limiting, response cache) -----
    redis_url: str = "redis://localhost:6379/0"

    # --- Security -----------------------------------------------------------
    jwt_secret: str = "CHANGE_ME_IN_PRODUCTION"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # --- Rate limiting (protects OUR api from being hammered) -------------
    rate_limit_per_minute: int = 120

    # --- Circuit breaker tuning ---------------------------------------------
    cb_failure_threshold: int = 5          # failures before opening the circuit
    cb_open_seconds: int = 60              # how long to stay OPEN before trying HALF_OPEN
    cb_window_seconds: int = 60            # rolling window for counting failures

    # --- Upstream free-tier API budgets (requests / window) ----------------
    coingecko_rate_limit: int = 25         # CoinGecko free tier ~ 10-30/min
    coingecko_rate_window_seconds: int = 60
    yfinance_rate_limit: int = 100
    yfinance_rate_window_seconds: int = 60

    # --- CORS ---------------------------------------------------------------
    allowed_origins: list[str] = ["http://localhost:5173", "http://localhost:8000"]

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, v: Any) -> list[str]:
        """Accept JSON array string '["a","b"]', plain '*', or comma-separated 'a,b'."""
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            v = v.strip()
            # JSON array: ["https://...", "https://..."]
            if v.startswith("["):
                import json
                return json.loads(v)
            # Bare wildcard or comma-separated list
            return [i.strip() for i in v.split(",") if i.strip()]
        return v

    # --- Caching layer (requirement 6) --------------------------------------
    # LIVE: tick-level price responses that must look fresh on the dashboard.
    # STATIC: non-volatile metadata (asset profiles, AI insights) -- cheap
    # to keep around far longer since it barely changes intraday.
    cache_ttl_live_seconds: int = 5
    cache_ttl_static_seconds: int = 3600

    # --- AI integration ------------------------------------------------------
    # Fully optional: if `anthropic_api_key` is unset, app/routers/insights.py
    # transparently falls back to a deterministic, pandas/numpy-based
    # narrative generator -- so the AI features work out of the box on the
    # free stack with zero external API calls or cost. Set the key to
    # upgrade insight quality with a real LLM.
    anthropic_api_key: str = ""
    ai_model: str = "claude-sonnet-4-6"

    mock_services: bool = False

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
