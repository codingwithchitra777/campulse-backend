from dataclasses import dataclass
import os
import json
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    firebase_sa_path: str = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "")
    firebase_credentials_json: str = os.getenv("FIREBASE_CREDENTIALS", "")  # Secret Manager
    firebase_project_id: str = os.getenv("FIREBASE_PROJECT_ID", "")
    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "1048965896991-dirq98278c5cj312k2o0kq3f307e2krf.apps.googleusercontent.com")
    csx_base_url: str = os.getenv("CSX_BASE_URL", "https://csx.com.kh")
    csx_lang: str = os.getenv("CSX_LANG", "en")
    default_user_id: str = os.getenv("DEFAULT_USER_ID", "u001")
    database_url: str = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5433/trading_journal")

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


