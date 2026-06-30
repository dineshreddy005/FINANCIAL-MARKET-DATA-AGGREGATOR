"""
Centralized configuration. Everything that varies between dev/staging/prod
(or that is a secret) lives in environment variables -- never hard-coded.
See .env.example for the full list.
"""
from functools import lru_cache
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
    # Stored as a raw string so pydantic-settings never tries to JSON-parse it.
    # Accepts: "*", "https://a.com", "https://a.com,https://b.com",
    # or a JSON array string '["https://a.com"]'.
    allowed_origins: str = "http://localhost:5173,http://localhost:8000"

    @property
    def cors_origins(self) -> list[str]:
        """Return a proper list of allowed origins for FastAPI CORSMiddleware."""
        import json
        v = self.allowed_origins.strip()
        if v.startswith("["):
            try:
                return json.loads(v)
            except Exception:
                pass
        return [i.strip() for i in v.split(",") if i.strip()]

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

    # --- Data Providers ------------------------------------------------------
    finnhub_api_key: str = ""

    mock_services: bool = False

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
