import os
from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_ENABLE_GOOGLE_SEARCH = _env_bool("GEMINI_ENABLE_GOOGLE_SEARCH", True)
GEMINI_REQUIRE_SEARCH_GROUNDING = _env_bool("GEMINI_REQUIRE_SEARCH_GROUNDING", True)

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
QUERY_CACHE_TTL_SECONDS = int(os.getenv("QUERY_CACHE_TTL_SECONDS", 1800))
LIVE_SEARCH_BYPASS_CACHE = _env_bool("LIVE_SEARCH_BYPASS_CACHE", GEMINI_ENABLE_GOOGLE_SEARCH)

AKTU_URL = "https://aktu.ac.in"
AKGEC_URL = "https://www.akgec.ac.in"
