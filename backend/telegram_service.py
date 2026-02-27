import requests
import logging
import config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

import os
import json
import fcntl

class TelegramService:
    def __init__(self):
        self.bot_token = config.TELEGRAM_BOT_TOKEN
        self.chat_ids = config.TELEGRAM_CHAT_IDS # Using plural as per config.py
        self._load_config()

    def _load_config(self):
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'telegram_config.json')
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    fcntl.flock(f, fcntl.LOCK_SH)
                    data = json.load(f)
                    if data.get("bot_token"):
                        self.bot_token = data["bot_token"]
                    if data.get("chat_ids"):
                        self.chat_ids = data["chat_ids"]
                    logger.info("Loaded Telegram configuration from telegram_config.json")
            except Exception as e:
                logger.error(f"Error loading telegram_config.json: {e}")
            finally:
                try: fcntl.flock(f, fcntl.LOCK_UN)
                except: pass

    def send_notification(self, job):
        """
        Sends a structured notification message to all configured Telegram chat IDs.
        Accepts a full job dict with enriched fields.
        """
        if not self.bot_token or self.bot_token == "your_bot_token_here" or not self.chat_ids:
            logger.warning("Telegram configuration is incomplete or using placeholders. Skipping notification.")
            return False

        # Extract fields safely
        title = job.get('title', 'N/A')
        company = job.get('company', 'N/A')
        apply_email = job.get('applyEmail', 'N/A')
        job_post_url = job.get('jobPostUrl') or job.get('viewFullPostUrl', '')

        # Hiring manager (can be string or dict)
        hiring_manager = job.get('hiringManager', {})
        if isinstance(hiring_manager, dict):
            hm_name = hiring_manager.get('name', 'N/A')
            hm_profile = hiring_manager.get('profileUrl', '')
        else:
            hm_name = str(hiring_manager) if hiring_manager else 'N/A'
            hm_profile = ''

        # Outreach messages
        outreach = job.get('outreach', {})
        initial_msg = outreach.get('initialMessage', '')
        followup_msg = outreach.get('followUpMessage1', '')

        # Build message
        lines = [
            "📌 <b>Job Processed</b>",
            "",
            f"🏢 <b>Company:</b> {company}",
            f"💼 <b>Role:</b> {title}",
            f"📧 <b>To:</b> {apply_email}",
        ]

        if job_post_url:
            lines.append(f"\n🔗 <b>Job URL:</b>\n{job_post_url}")

        lines.append(f"\n👤 <b>Hiring Manager:</b>\n{hm_name}")
        if hm_profile:
            lines.append(f"{hm_profile}")

        if initial_msg:
            lines.append(f"\n📩 <b>Initial Message:</b>\n{initial_msg}")

        if followup_msg:
            lines.append(f"\n🔁 <b>Follow-up 1:</b>\n{followup_msg}")

        message = '\n'.join(lines)

        success = True
        for chat_id in self.chat_ids:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            }

            try:
                response = requests.post(url, json=payload, timeout=10)
                response.raise_for_status()
                logger.info(f"Telegram notification sent successfully to {chat_id}.")
            except Exception as e:
                logger.error(f"Failed to send Telegram notification to {chat_id}: {e}")
                success = False
        
        return success
