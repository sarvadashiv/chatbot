import os
from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# Free-tier text generation Gemini models (kept configurable via GEMINI_FALLBACK_MODELS).
DEFAULT_FREE_TIER_GEMINI_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
]


TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY", "")
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_FALLBACK_MODELS = _env_csv(
    "GEMINI_FALLBACK_MODELS",
    ",".join(DEFAULT_FREE_TIER_GEMINI_MODELS),
)
GEMINI_ENABLE_GOOGLE_SEARCH = _env_bool("GEMINI_ENABLE_GOOGLE_SEARCH", True)
GEMINI_REQUIRE_SEARCH_GROUNDING = _env_bool("GEMINI_REQUIRE_SEARCH_GROUNDING", True)
GEMINI_REQUEST_RETRIES = int(os.getenv("GEMINI_REQUEST_RETRIES", 2))
GEMINI_RETRY_BACKOFF_SECONDS = float(os.getenv("GEMINI_RETRY_BACKOFF_SECONDS", 1.25))

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
QUERY_CACHE_TTL_SECONDS = int(os.getenv("QUERY_CACHE_TTL_SECONDS", 1800))
LIVE_SEARCH_BYPASS_CACHE = _env_bool("LIVE_SEARCH_BYPASS_CACHE", GEMINI_ENABLE_GOOGLE_SEARCH)
