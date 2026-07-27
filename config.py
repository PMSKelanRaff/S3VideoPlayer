import json
import os
from dataclasses import dataclass
from pathlib import Path

REQUIRED_KEYS = [
    "region",
    "user_pool_id",
    "app_client_id",
    "identity_pool_id",
    "cross_account_role_arn",
    "bucket_name",
    "bucket_region",
]

@dataclass(frozen=True)
class AppConfig:
    region: str
    user_pool_id: str
    app_client_id: str
    identity_pool_id: str
    cross_account_role_arn: str
    bucket_name: str
    bucket_region: str

def load_config(config_path: str | Path = "config.json") -> AppConfig:
    """Loads runtime configuration from config.json."""
    config_path = Path(config_path)
    data: dict[str, str] = {}
    
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

    for key in REQUIRED_KEYS:
        env_value = os.environ.get(key.upper())
        if env_value:
            data[key] = env_value

    missing = [key for key in REQUIRED_KEYS if not data.get(key)]
    if missing:
        raise ValueError(
            f"Missing required config values: {', '.join(missing)}. "
            f"Set them in {config_path} or as environment variables."
        )

    return AppConfig(**{key: data[key] for key in REQUIRED_KEYS})