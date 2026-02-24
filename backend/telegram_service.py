import requests
import logging
import config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TelegramService:
    def __init__(self):
        self.bot_token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID

    def send_notification(self, title, company, apply_email):
        """
        Sends a notification message to Telegram.
        """
        if not self.bot_token or self.bot_token == "your_bot_token_here" or not self.chat_id or self.chat_id == "your_chat_id_here":
            logger.warning("Telegram configuration is incomplete or using placeholders. Skipping notification.")
            return False

        message = (
            "✅ Email Sent\n"
            f"Job: {title}\n"
            f"Company: {company}\n"
            f"To: {apply_email}"
        )

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML"
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info("Telegram notification sent successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False
