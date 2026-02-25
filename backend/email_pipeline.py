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

def send_telegram_notification(message, document_path=None):
    """
    Sends a notification message or document to all configured Telegram chat IDs.
    """
    bot_token = config.TELEGRAM_BOT_TOKEN
    chat_ids = config.TELEGRAM_CHAT_IDS

    if not bot_token or not chat_ids:
        logger.warning("Telegram configuration is incomplete (missing token or chat IDs). Skipping.")
        return False

    success = True
    for chat_id in chat_ids:
        try:
            if document_path and os.path.exists(document_path):
                # Send as document with caption
                url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
                with open(document_path, 'rb') as doc:
                    files = {'document': doc}
                    data = {
                        "chat_id": chat_id,
                        "caption": message,
                        "parse_mode": "HTML"
                    }
                    response = requests.post(url, data=data, files=files, timeout=20)
            else:
                # Send as simple message
                url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                payload = {
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "HTML"
                }
                response = requests.post(url, json=payload, timeout=10)

            response.raise_for_status()
            logger.info(f"Telegram notification sent successfully to {chat_id}.")
        except Exception as e:
            logger.error(f"Failed to send Telegram notification to {chat_id}: {e}")
            success = False

    return success
