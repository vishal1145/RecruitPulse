"""
followup_service.py

Runs the hourly background job that:
  1. Detects if a Gmail draft was manually sent by the user.
  2. Creates a follow-up draft if the original was not sent within followUpDays.
  3. Sends Telegram notifications for every state change.
"""

import json
import os
import logging
from datetime import datetime, timedelta

from gmail_service import GmailService
import email_pipeline

logger = logging.getLogger(__name__)

JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'jobs.json')


# ─── JSON Helpers ─────────────────────────────────────────────────────────────

def _load_jobs():
    if not os.path.exists(JSON_PATH):
        return []
    with open(JSON_PATH, 'r') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def _save_jobs(jobs):
    with open(JSON_PATH, 'w') as f:
        json.dump(jobs, f, indent=4)


# ─── Core Logic ───────────────────────────────────────────────────────────────

def check_followups():
    """
    Main function scheduled to run every hour. Iterates all jobs and:
      - Marks sent if user manually sent the draft.
      - Creates follow-up draft if due date has passed and draft was not sent.
    """
    logger.info("⏰ Running follow-up check...")

    try:
        gmail = GmailService()
    except Exception as e:
        logger.error(f"Failed to initialize GmailService in check_followups: {e}")
        return

    jobs = _load_jobs()
    updated = False

    for job in jobs:
        job_title = job.get('title', 'Unknown')
        job_id = job.get('jobId', '')

        # ── 1. Detect if user manually sent the draft ──────────────────────────
        thread_id = job.get('gmailThreadId')
        if not job.get('emailSent') and thread_id:
            try:
                if gmail.was_email_sent(thread_id):
                    job['emailSent'] = True
                    job['emailSentAt'] = datetime.utcnow().isoformat()
                    job['followUpCancelled'] = True
                    updated = True
                    logger.info(f"✅ Send detected for job: {job_title}")

                    # Check if reply received too
                    if gmail.check_reply_received(thread_id):
                        job['replyReceived'] = True
                        logger.info(f"📩 Reply also detected for job: {job_title}")

                    email_pipeline.send_telegram_notification(
                        f"✅ <b>Email Sent Detected</b>\n"
                        f"Job: {job_title}\n"
                        f"Company: {job.get('company', '')}\n"
                        f"To: {job.get('applyEmail', '')}"
                    )
                    continue  # No need to check follow-up for this job
            except Exception as e:
                logger.error(f"Error checking sent status for job {job_id}: {e}")

        # ── 2. Follow-up Logic ─────────────────────────────────────────────────
        if (
            not job.get('emailSent')
            and not job.get('followUpSent')
            and not job.get('followUpCancelled')
            and job.get('draftCreatedAt')
            and job.get('applyEmail')
            and job.get('applyEmail') != 'not-provided'
        ):
            try:
                draft_time = datetime.fromisoformat(job['draftCreatedAt'])
                follow_days = job.get('followUpDays', 3)
                follow_due = draft_time + timedelta(days=follow_days)

                if datetime.utcnow() >= follow_due:
                    logger.info(f"⏳ Follow-up due for job: {job_title}")

                    followup_body = (
                        f"Hi,\n\n"
                        f"I wanted to follow up on my application for the {job_title} role at {job.get('company', '')}.\n"
                        f"I remain very interested in the opportunity and would love to discuss further.\n\n"
                        f"Looking forward to hearing from you.\n\n"
                        f"Best regards"
                    )

                    followup_subject = f"Follow-up: {job.get('emailSubject', f'Application for {job_title}')}"

                    success, metadata = email_pipeline.send_email_with_attachment.__wrapped__(job, None) \
                        if hasattr(email_pipeline.send_email_with_attachment, '__wrapped__') \
                        else _create_followup_draft(gmail, job, followup_subject, followup_body)

                    if success:
                        job['followUpSent'] = True
                        job['lastFollowUpAt'] = datetime.utcnow().isoformat()
                        if metadata.get('gmailDraftId'):
                            job['followUpDraftId'] = metadata['gmailDraftId']
                        updated = True

                        email_pipeline.send_telegram_notification(
                            f"⏳ <b>Follow-up Draft Created</b>\n"
                            f"Job: {job_title}\n"
                            f"Company: {job.get('company', '')}\n"
                            f"To: {job.get('applyEmail', '')}"
                        )
                    else:
                        logger.error(f"Failed to create follow-up draft for job: {job_title}")
                        email_pipeline.send_telegram_notification(
                            f"❌ <b>Follow-up Draft Failed</b>\n"
                            f"Job: {job_title}\n"
                            f"Error: Could not create follow-up draft"
                        )
            except Exception as e:
                logger.error(f"Error in follow-up logic for job {job_id}: {e}")

    if updated:
        _save_jobs(jobs)
        logger.info("✅ jobs.json updated after follow-up check.")
    else:
        logger.info("✅ Follow-up check complete. No updates needed.")


def _create_followup_draft(gmail, job, subject, body):
    """
    Creates a plain follow-up draft (no PDF attachment).
    Returns (success, metadata_dict).
    """
    try:
        success, result = gmail.create_draft(
            to_email=job.get('applyEmail'),
            subject=subject,
            body=body,
            attachment_path=None
        )
        if success:
            metadata = {
                'gmailDraftId': result.get('id'),
                'gmailThreadId': result.get('message', {}).get('threadId'),
            }
            return True, metadata
        return False, {}
    except Exception as e:
        logger.error(f"_create_followup_draft error: {e}")
        return False, {}
