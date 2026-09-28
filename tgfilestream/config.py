"""Environment-driven configuration.

Every setting can be overridden through environment variables so the same
codebase runs as a local dev server, a Docker container or a PaaS deploy
without any code changes.
"""
from __future__ import annotations

import os
import sys

from yarl import URL


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        print(f"Please make sure the {name} environment variable is an integer")
        sys.exit(1)
    if not 1 <= value <= 65535:
        print(f"Please make sure the {name} environment variable is between 1 and 65535")
        sys.exit(1)
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --- HTTP server -----------------------------------------------------------
port = _env_int("PORT", 8080)
host = os.environ.get("HOST", "localhost")
public_url = URL(os.environ.get("PUBLIC_URL", f"http://{host}:{port}"))

# --- Telegram --------------------------------------------------------------
api_id = os.environ.get("TG_API_ID")
api_hash = os.environ.get("TG_API_HASH")
session_name = os.environ.get("TG_SESSION_NAME", "tgfilestream")

# --- Behaviour -------------------------------------------------------------
# Maximum number of concurrent downloads a single IP may have active.
request_limit = _env_int("REQUEST_LIMIT", 5)
# Maximum number of connections opened to a single Telegram datacenter.
connection_limit = _env_int("CONNECTION_LIMIT", 20)
# Seconds a generated download link stays valid. 0 = never expires.
link_ttl = _env_int("LINK_TTL", 0)
# One-time links are invalidated after the first successful download.
one_time_links = _env_bool("ONE_TIME_LINKS", False)
# Serve a fake in-memory file store instead of talking to Telegram.
# Lets the whole stack run (and be tested) with zero API credentials.
demo_mode = _env_bool("DEMO_MODE", False)
# Trust X-Forwarded-For / X-Forwarded-Proto when sitting behind a proxy.
trust_forward_headers = _env_bool("TRUST_FORWARD_HEADERS")

start_message = os.environ.get(
    "TG_START_MESG",
    "Send me a file and I will reply with a direct download link.",
)
group_chat_message = os.environ.get(
    "TG_G_C_MESG",
    "Send files in a private chat to get download links.",
)

# --- Logging ---------------------------------------------------------------
debug = _env_bool("DEBUG")
log_config = os.environ.get("LOG_CONFIG")
