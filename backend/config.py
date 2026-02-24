import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# SMTP Configuration
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL")

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
_chat_ids_str = os.getenv("TELEGRAM_CHAT_IDS", "")
# Split by comma and filter out empty strings
TELEGRAM_CHAT_IDS = [cid.strip() for cid in _chat_ids_str.split(",") if cid.strip()]

# Server Configuration
BASE_URL = os.getenv("BASE_URL")

# Gmail OAuth Configuration
GMAIL_CLIENT_ID = os.getenv("GMAIL_CLIENT_ID")
GMAIL_PROJECT_ID = os.getenv("GMAIL_PROJECT_ID")
GMAIL_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET")
GMAIL_AUTH_URI = os.getenv("GMAIL_AUTH_URI")
GMAIL_TOKEN_URI = os.getenv("GMAIL_TOKEN_URI")
GMAIL_AUTH_PROVIDER_X509_CERT_URL = os.getenv("GMAIL_AUTH_PROVIDER_X509_CERT_URL")
GMAIL_REDIRECT_URIS = [uri.strip() for uri in os.getenv("GMAIL_REDIRECT_URIS", "").split(",") if uri.strip()]
