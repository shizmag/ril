import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env (search up from CWD first)
def _load_env_from_cwd():
    current = Path.cwd()
    for parent in [current] + list(current.parents):
        env_path = parent / ".env"
        if env_path.is_file():
            load_dotenv(dotenv_path=env_path)
            return True
    return False

if not _load_env_from_cwd():
    load_dotenv()

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LIBRARY_DIR = BASE_DIR / "library"

# Configurations
LIBRARY_DIR = Path(os.getenv("RIL_LIBRARY_DIR", str(DEFAULT_LIBRARY_DIR)))
DB_PATH = Path(os.getenv("RIL_DB_PATH", str(LIBRARY_DIR / "metadata.db")))

# Ensure directories exist
LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
(LIBRARY_DIR / "images").mkdir(parents=True, exist_ok=True)

# Telegram Config
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
# Allowed user IDs list for security (comma-separated string, e.g. "123456,789012")
ALLOWED_TELEGRAM_USERS = []
raw_users = os.getenv("ALLOWED_TELEGRAM_USERS")
if raw_users:
    try:
        ALLOWED_TELEGRAM_USERS = [int(u.strip()) for u in raw_users.split(",") if u.strip()]
    except ValueError:
        print("Warning: ALLOWED_TELEGRAM_USERS is not formatted correctly. It should be a list of integers.")

# Playwright configs
CRAWLER_HEADLESS = os.getenv("RIL_CRAWLER_HEADLESS", "true").lower() == "true"
CRAWLER_STEALTH = os.getenv("RIL_CRAWLER_STEALTH", "true").lower() == "true"
CRAWLER_TIMEOUT_MS = int(os.getenv("RIL_CRAWLER_TIMEOUT_MS", "30000"))
