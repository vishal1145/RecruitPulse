import json
import os
import fcntl
import logging
from datetime import datetime
from mail_service import MailService
from telegram_service import TelegramService

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class JobEmailService:
    def __init__(self, json_file_path):
        self.json_file_path = json_file_path
        self.mail_service = MailService()
        self.telegram_service = TelegramService()

    def _load_jobs(self):
        if not os.path.exists(self.json_file_path):
            return []
        
        with open(self.json_file_path, 'r') as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _save_jobs(self, jobs):
        # We use a temporary file and rename it to avoid corruption during write
        temp_file = self.json_file_path + ".tmp"
        with open(temp_file, 'w') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(jobs, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        
        # Atomic rename
        os.replace(temp_file, self.json_file_path)

    def send_pending_emails(self):
        """
        Loads jobs, filters for those ready to be sent, sends them, and updates the JSON.
        """
        jobs = self._load_jobs()
        
        success_count = 0
        failed_count = 0
        processed_count = 0

        # Filter jobs: jdResumeBuilt == True AND (emailSent is missing or False)
        pending_jobs = [
            job for job in jobs
            if not job.get('emailSent', False)
        ]

        if not pending_jobs:
            logger.info("No pending emails to send.")
            return {
                "total_processed": 0,
                "success_count": 0,
                "failed_count": 0
            }

        logger.info(f"Found {len(pending_jobs)} pending emails.")

        for job in pending_jobs:
            processed_count += 1
            job_id = job.get('jobId')
            apply_email = job.get('applyEmail')
            subject = job.get('emailSubject')
            body = job.get('emailBody')

            if not apply_email or not subject or not body:
                logger.warning(f"Job {job_id} is missing email details. Skipping.")
                failed_count += 1
                continue

            # Try to send the email
            is_sent = self.mail_service.send_email(apply_email, subject, body)

            if is_sent:
                # Update job status
                job['emailSent'] = True
                job['emailSentAt'] = datetime.utcnow().isoformat()
                success_count += 1
                logger.info(f"Successfully processed email for job {job_id}")
                
                # Send Telegram notification
                title = job.get('title', 'Unknown Job')
                company = job.get('company', 'Unknown Company')
                self.telegram_service.send_notification(title, company, apply_email)
            else:
                failed_count += 1
                logger.error(f"Failed to send email for job {job_id}")

        # Save all changes back to JSON
        if success_count > 0:
            self._save_jobs(jobs)
            logger.info(f"Updated {success_count} jobs in {self.json_file_path}")

        return {
            "total_processed": processed_count,
            "success_count": success_count,
            "failed_count": failed_count
        }
