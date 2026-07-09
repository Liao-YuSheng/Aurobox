import os

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal test environments
    def load_dotenv(*args, **kwargs):
        return False


load_dotenv()


def load_config():
    """Load PUDU API configuration from environment variables."""
    return {
        "PUDU_BASE_URL": os.getenv("PUDU_BASE_URL", "https://css-open-platform.pudutech.com"),
        "APP_KEY": os.getenv("Pd_key"),
        "APP_SECRET": os.getenv("Pd_secret"),
        "SHOP_ID": os.getenv("Aurotek_id"),
        "DEFAULT_SN": os.getenv("FLASHBOT_SN", "8FF055923050007"),
        "DEFAULT_MAP_NAME": os.getenv("DEFAULT_MAP_NAME", ""),
    }


def require_config(config):
    missing = [name for name in ("APP_KEY", "APP_SECRET") if not config.get(name)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}"
        )
    return config
