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
        Returns (True, draft_object) on success, (False, error_str) on failure.
        The draft object contains 'id' and 'message.threadId'.
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

            draft_id = draft.get('id')
            thread_id = draft.get('message', {}).get('threadId')
            logger.info(f"Draft created successfully. Draft ID: {draft_id}, Thread ID: {thread_id}")
            return True, draft

        except Exception as e:
            logger.error(f"Failed to create Gmail draft: {e}")
            return False, str(e)

    def was_email_sent(self, thread_id):
        """
        Checks whether the email in the given thread was manually sent by the user.
        Returns True if a sent message exists in that thread, False otherwise.
        """
        try:
            results = self.service.users().messages().list(
                userId='me',
                q=f'thread:{thread_id} in:sent'
            ).execute()
            return 'messages' in results and len(results['messages']) > 0
        except Exception as e:
            logger.error(f"Error checking sent status for thread {thread_id}: {e}")
            return False

    def check_reply_received(self, thread_id):
        """
        Checks whether a reply was received in this email thread.
        Returns True if thread has more than 1 message (meaning someone replied).
        """
        try:
            thread = self.service.users().threads().get(userId='me', id=thread_id).execute()
            messages = thread.get('messages', [])
            return len(messages) > 1
        except Exception as e:
            logger.error(f"Error checking reply for thread {thread_id}: {e}")
            return False

    def delete_draft(self, draft_id):
        """
        Deletes a Gmail draft by its ID.
        Returns True on success, False on failure.
        """
        try:
            self.service.users().drafts().delete(userId='me', id=draft_id).execute()
            logger.info(f"Deleted Gmail draft: {draft_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete draft {draft_id}: {e}")
            return False

    def create_draft_in_thread(self, to_email, subject, body, attachment_path, thread_id):
        """
        Creates a new draft in an existing thread.
        This preserves the email conversation context.
        Returns (True, draft_object) on success, (False, error_str) on failure.
        """
        try:
            message = MIMEMultipart()
            message['to'] = to_email
            message['subject'] = subject

            message.attach(MIMEText(body, 'plain'))

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

            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')

            draft_body = {
                'message': {
                    'raw': raw_message,
                    'threadId': thread_id,
                }
            }

            draft = self.service.users().drafts().create(userId='me', body=draft_body).execute()
            draft_id = draft.get('id')
            new_thread_id = draft.get('message', {}).get('threadId')
            logger.info(f"Draft created in thread {thread_id}. Draft ID: {draft_id}, Thread ID: {new_thread_id}")
            return True, draft

        except Exception as e:
            logger.error(f"Failed to create draft in thread {thread_id}: {e}")
            return False, str(e)

