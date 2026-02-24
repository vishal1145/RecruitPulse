import smtplib
import logging
import requests
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
import os
import config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def send_email_with_attachment(job, pdf_path):
    """
    Sends an email with a PDF attachment using SMTP.
    """
    to_email = job.get('applyEmail')
    subject = job.get('emailSubject')
    body = job.get('emailBody')
    
    if not to_email or to_email == "not-provided":
        logger.warning(f"No applyEmail provided for job {job.get('jobId')}. Skipping email.")
        return False

    if not os.path.exists(pdf_path):
        logger.error(f"PDF file not found at {pdf_path}")
        return False

    # Create the root message
    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = config.SMTP_FROM_EMAIL
    msg['To'] = to_email

    # Attach the body
    msg.attach(MIMEText(body, 'plain'))

    # Attach the PDF
    try:
        with open(pdf_path, "rb") as f:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                'Content-Disposition',
                f'attachment; filename="{os.path.basename(pdf_path)}"',
            )
            msg.attach(part)
    except Exception as e:
        logger.error(f"Failed to attach PDF: {e}")
        return False

    try:
        logger.info(f"Connecting to SMTP server {config.SMTP_HOST}:{config.SMTP_PORT}")
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
            server.set_debuglevel(0)
            server.starttls()
            logger.info("Logging into SMTP server...")
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            logger.info(f"Sending email to {to_email} with attachment...")
            server.send_message(msg)
            logger.info("Email sent successfully!")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False

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
            success = False # Mark as failed if any one fails, but keep trying others

    return success
