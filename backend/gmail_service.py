import base64
import os
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from gmail_auth import get_gmail_service

logger = logging.getLogger(__name__)

class GmailService:
    def __init__(self):
        self.service = get_gmail_service()

    def create_draft(self, to_email, subject, body, attachment_path=None):
        """
        Creates a draft email in Gmail.
        """
        try:
            # Create the message
            message = MIMEMultipart()
            message['to'] = to_email
            message['subject'] = subject
            
            # Attach body
            message.attach(MIMEText(body, 'plain'))

            # Attach PDF if provided
            if attachment_path and os.path.exists(attachment_path):
                with open(attachment_path, "rb") as f:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header(
                        'Content-Disposition',
                        f'attachment; filename="{os.path.basename(attachment_path)}"',
                    )
                    message.attach(part)
            
            # Encode message
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
            
            # Create draft
            draft_body = {
                'message': {
                    'raw': raw_message
                }
            }
            
            draft = self.service.users().drafts().create(userId='me', body=draft_body).execute()
            
            logger.info(f"Draft created successfully. Draft ID: {draft['id']}")
            return True, draft
            
        except Exception as e:
            logger.error(f"Failed to create Gmail draft: {e}")
            return False, str(e)
