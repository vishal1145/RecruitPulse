import smtplib
import logging
from email.message import EmailMessage
import config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MailService:
    def __init__(self):
        self.host = config.SMTP_HOST
        self.port = config.SMTP_PORT
        self.username = config.SMTP_USERNAME
        self.password = config.SMTP_PASSWORD
        self.from_email = config.SMTP_FROM_EMAIL

    def send_email(self, to_email, subject, body):
        """
        Sends an email using SMTP with STARTTLS.
        """
        if not all([self.host, self.username, self.password, self.from_email]):
            logger.error("SMTP configuration is incomplete. Check environment variables.")
            raise ValueError("Incomplete SMTP configuration")

        msg = EmailMessage()
        msg.set_content(body)
        msg['Subject'] = subject
        msg['From'] = self.from_email
        msg['To'] = to_email

        try:
            logger.info(f"Connecting to SMTP server {self.host}:{self.port}")
            # Use SMTP for port 587 (typically STARTTLS)
            with smtplib.SMTP(self.host, self.port) as server:
                server.set_debuglevel(0)
                server.starttls()  # Upgrade the connection to secure
                logger.info("Logging into SMTP server...")
                server.login(self.username, self.password)
                logger.info(f"Sending email to {to_email}...")
                server.send_message(msg)
                logger.info("Email sent successfully!")
            return True
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            return False
