from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """T.A.R.S. configuration loaded from environment variables."""

    # --- Database ---
    database_url: str

    # --- Redis ---
    redis_url: str = "redis://100.119.114.125:6379/0"

    # --- ChromaDB ---
    chromadb_url: str = "http://100.119.114.125:8200"
    chroma_auth_token: str

    # --- TARS API ---
    tars_api_key: str
    allowed_device_tokens: str = ""

    # --- AI Models ---
    gemini_api_key: str

    # --- Telegram ---
    telegram_bot_token: str
    telegram_chat_id: str = ""

    # --- Gmail (base64-encoded OAuth JSON) ---
    gmail_personal_credentials: str
    gmail_professional_credentials: str

    # --- iCloud CalDAV ---
    icloud_caldav_user: str
    icloud_caldav_password: str

    # --- GitHub ---
    github_pat: str

    # --- Notion ---
    notion_token: str

    # --- Teller ---
    teller_access_token: str
    teller_cert_path: str = "/etc/tars/teller/cert.pem"
    teller_key_path: str = "/etc/tars/teller/key.pem"
    teller_env: str = "sandbox"  # sandbox | development | production

    # --- Weather ---
    openweathermap_api_key: str
    default_location: str = "Wooster,OH,US"

    # --- Picovoice ---
    picovoice_access_key: str

    # --- Wake Word ---
    wake_word_model_paths: list[str] = [
        "/data/models/hey-tars_linux.ppn",
        "/data/models/tars_linux.ppn",
    ]
    wake_word_sensitivity: float = 0.6
    wake_word_silence_threshold: int = 500
    wake_word_silence_duration: float = 1.5
    wake_word_max_record_seconds: float = 15.0
    whisper_model: str = "base"
    homepod_host: str = ""
    usb_mic_device_index: int | None = None

    # --- APNs ---
    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_bundle_id: str = ""
    apns_key_path: str = "/secrets/apns-key.p8"
    apns_use_sandbox: bool = False

    # --- Cloudflare ---
    cloudflare_tunnel_token: str = ""

    # --- Grafana / Loki ---
    grafana_url: str = ""
    grafana_api_key: str = ""
    loki_url: str = ""

    # --- SerpAPI ---
    serpapi_key: str = ""

    # --- Brave Search ---
    brave_api_key: str = ""

    # --- System ---
    node_role: str = "brain"
    log_level: str = "INFO"
    debug: bool = False

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance. Usable as a FastAPI dependency."""
    return Settings()
