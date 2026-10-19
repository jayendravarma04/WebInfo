from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Gemini ---
    # Comma-separated. With 2 keys the agent rotates when one hits a 429.
    gemini_api_keys: str = ""
    # Leave blank to auto-pick the best free Flash-Lite model via ListModels at startup.
    gemini_model: str = ""
    gemini_min_interval_s: float = 4.0       # spacing between calls per key (free-tier RPM safety)
    gemini_daily_cap: int = 400              # hard cap per key per UTC day (free Flash-Lite ≈ 500/day)

    # --- Tavily ---
    tavily_api_key: str = ""
    tavily_monthly_cap: int = 900            # free plan = 1000 credits/month; leave headroom
    tavily_max_results: int = 5

    # --- Agent ---
    max_pages: int = 3                       # pages visited per question
    max_hops: int = 1                        # extra link-follow navigations if the answer is incomplete
    page_timeout_ms: int = 15000
    page_text_chars: int = 9000              # per-page text budget sent to Gemini
    answer_cache_hours: int = 12
    search_cache_hours: int = 24
    per_ip_hourly_limit: int = 15

    # --- Infra ---
    database_url: str = "postgresql://webinfo:webinfo@db:5432/webinfo"
    cors_origins: str = "*"
    allow_private_hosts: bool = False        # tests only — keeps the agent off the GCP metadata server
    screenshot_quality: int = 55             # JPEG quality for streamed screenshots
    viewport_w: int = 1280
    viewport_h: int = 800

    @property
    def gemini_keys(self) -> list[str]:
        return [k.strip() for k in self.gemini_api_keys.split(",") if k.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
