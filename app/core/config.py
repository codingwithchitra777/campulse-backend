from dataclasses import dataclass
import os
import json
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_group_chat_id: str = os.getenv("TELEGRAM_GROUP_CHAT_ID", "")
    telegram_webhook_secret: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    telegram_bot_username: str = os.getenv("TELEGRAM_BOT_USERNAME", "CamboPulseBot")
    deploy_notify_secret: str = os.getenv("DEPLOY_NOTIFY_SECRET", "")
    firebase_sa_path: str = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "")
    firebase_credentials_json: str = os.getenv("FIREBASE_CREDENTIALS", "")  # Secret Manager
    firebase_project_id: str = os.getenv("FIREBASE_PROJECT_ID", "")
    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "1048965896991-dirq98278c5cj312k2o0kq3f307e2krf.apps.googleusercontent.com")
    csx_base_url: str = os.getenv("CSX_BASE_URL", "https://csx.com.kh")
    csx_lang: str = os.getenv("CSX_LANG", "en")
    # International equities provider (Phase 2). Unused until a US instrument exists.
    finnhub_api_key: str = os.getenv("FINNHUB_API_KEY", "")
    finnhub_base_url: str = os.getenv("FINNHUB_BASE_URL", "https://finnhub.io/api/v1")
    default_user_id: str = os.getenv("DEFAULT_USER_ID", "u001")
    database_url: str = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5433/trading_journal")
    redis_url: str = os.getenv("REDIS_URL", "redis://default:FIwEoRahdTexVeF2MlgQRyT2XjhwIUuJ@time-camera-show-26003.db.redis.io:11914")
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-insecure-secret-change-me-in-production")
    jwt_expire_days: int = int(os.getenv("JWT_EXPIRE_DAYS", "30"))

    def get_firebase_credentials(self):
        """Get Firebase credentials from file or env var (Secret Manager)."""
        if self.firebase_credentials_json:
            # Running in Cloud Run with Secret Manager
            return json.loads(self.firebase_credentials_json)
        elif self.firebase_sa_path and os.path.exists(self.firebase_sa_path):
            # Running locally with service account file
            with open(self.firebase_sa_path) as f:
                return json.load(f)
        else:
            raise ValueError("No Firebase credentials found")

settings = Settings()


