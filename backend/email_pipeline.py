import logging
import requests
import os
import config
from gmail_service import GmailService

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def send_email_with_attachment(job, pdf_path):
    """
    Creates a Gmail draft with a PDF attachment.
    Returns (success: bool, metadata: dict) on success.
    metadata contains gmailDraftId and gmailThreadId.
    """
    to_email = job.get('applyEmail')
    subject = job.get('emailSubject')
    body = job.get('emailBody')

    if not to_email or to_email == "not-provided":
        logger.warning(f"No applyEmail provided for job {job.get('jobId')}. Skipping draft creation.")
        return False, {}

    if not os.path.exists(pdf_path):
        logger.error(f"PDF file not found at {pdf_path}")
        return False, {}

    try:
        logger.info(f"Creating Gmail draft for {to_email}...")
        gmail_service = GmailService()
        success, result = gmail_service.create_draft(to_email, subject, body, pdf_path)

        if success:
            draft_id = result.get('id')
            thread_id = result.get('message', {}).get('threadId')
            logger.info(f"Gmail draft created! Draft ID: {draft_id}, Thread ID: {thread_id}")
            metadata = {
                'gmailDraftId': draft_id,
                'gmailThreadId': thread_id,
            }
            return True, metadata
        else:
            logger.error(f"Failed to create Gmail draft: {result}")
            return False, {}
    except Exception as e:
        logger.error(f"Error in Gmail draft creation pipeline: {e}")
        return False, {}

def send_telegram_notification(message, document_path=None, reply_markup=None):
    """
    Sends a notification message or document to all configured Telegram chat IDs.
    Optionally includes an inline keyboard via reply_markup.
    """
    bot_token = config.TELEGRAM_BOT_TOKEN
    chat_ids = config.TELEGRAM_CHAT_IDS

    # Try to load dynamic config
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'telegram_config.json')
    if os.path.exists(config_path):
        import fcntl
        import json
        try:
            with open(config_path, 'r') as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                data = json.load(f)
                if data.get("bot_token"):
                    bot_token = data["bot_token"]
                if data.get("chat_ids"):
                    chat_ids = data["chat_ids"]
        except Exception as e:
            logger.error(f"Error loading dynamic telegram config: {e}")
        finally:
            try: fcntl.flock(f, fcntl.LOCK_UN)
            except: pass

    if not bot_token or not chat_ids:
        logger.warning("Telegram configuration is incomplete. Skipping.")
        return False

    success = True
    for chat_id in chat_ids:
        try:
            if document_path and os.path.exists(document_path):
                # Send text message first (with inline keyboard if provided), then document
                msg_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                msg_payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
                if reply_markup:
                    msg_payload["reply_markup"] = reply_markup
                requests.post(msg_url, json=msg_payload, timeout=10).raise_for_status()

                doc_url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
                with open(document_path, 'rb') as doc:
                    files = {'document': doc}
                    doc_data = {"chat_id": chat_id, "caption": "📎 Resume PDF", "parse_mode": "HTML"}
                    requests.post(doc_url, data=doc_data, files=files, timeout=20).raise_for_status()
            else:
                # No document — send as simple message
                url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
                if reply_markup:
                    payload["reply_markup"] = reply_markup
                response = requests.post(url, json=payload, timeout=10)
                response.raise_for_status()

            logger.info(f"Telegram notification sent successfully to {chat_id}.")
        except Exception as e:
            logger.error(f"Failed to send Telegram notification to {chat_id}: {e}")
            success = False

    return success
