from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import json
import requests as http_requests
from datetime import datetime
import fcntl
import logging
import base64 as b64
import threading
import time
from job_email_service import JobEmailService
from pdf_service import PdfService
import email_pipeline
import config
from gmail_service import GmailService
import scheduler  # Starts APScheduler background job on import

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB for PDF base64 payloads
# Enable CORS for all origins so the extension can POST data
CORS(app)

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE_PATH = os.path.join(BASE_DIR, 'jobs.json')
TELEGRAM_CONFIG_PATH = os.path.join(BASE_DIR, 'telegram_config.json')
PDF_OUTPUT_DIR = os.path.join(BASE_DIR, 'generated_pdfs')

# Initialize PDF Service
pdf_service = PdfService(PDF_OUTPUT_DIR)

# Port Configuration
PORT = int(os.environ.get('PORT', 5350))

def load_jobs_from_json():
    if not os.path.exists(JSON_FILE_PATH):
        return []
    
    # If the file exists but is empty (0 bytes), return empty list
    if os.path.getsize(JSON_FILE_PATH) == 0:
        return []

    # Do NOT catch exceptions here (unless it's empty); let them bubble up 
    # so we don't overwrite corrupted data with an empty list.
    with open(JSON_FILE_PATH, 'r') as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            return json.load(f)
        except json.JSONDecodeError:
            # Re-check size under lock; if still 0, it's empty. 
            # If not 0, it's actual corrupted JSON, so we raise.
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                return []
            raise
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

def save_jobs_to_json(jobs):
    with open(JSON_FILE_PATH, 'w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            json.dump(jobs, f, indent=4)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

def save_telegram_config(config_data):
    """Saves Telegram configuration to a local JSON file."""
    if not config_data:
        return
    
    # Format to expected internal structure
    formatted_config = {
        "bot_token": config_data.get("botToken"),
        "chat_ids": [cid.strip() for cid in config_data.get("chatIds", "").split(",") if cid.strip()]
    }
    
    if not formatted_config["bot_token"] or not formatted_config["chat_ids"]:
        logger.warning("Incomplete Telegram config received. Not saving.")
        return

    with open(TELEGRAM_CONFIG_PATH, 'w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            json.dump(formatted_config, f, indent=4)
            logger.info(f"Saved Telegram configuration to {TELEGRAM_CONFIG_PATH}")
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

def update_job_sent_status(job_id):
    """Updates jobs.json to mark a job draft as created."""
    try:
        jobs = load_jobs_from_json()
        updated = False
        for job in jobs:
            if job.get('jobId') == job_id:
                job['emailSent'] = True
                job['draftCreated'] = True
                job['draftCreatedAt'] = datetime.utcnow().isoformat()
                updated = True
                break

        if updated:
            save_jobs_to_json(jobs)
        return updated
    except Exception as e:
        logger.error(f"Error updating jobs.json for {job_id}: {e}")
        return False

def _update_job_with_draft_metadata(job_id, draft_metadata):
    """
    Saves Gmail draft metadata and follow-up defaults into jobs.json
    after a draft is successfully created.
    """
    try:
        jobs = load_jobs_from_json()
        updated = False
        for job in jobs:
            if job.get('jobId') == job_id:
                job['draftCreated'] = True
                job['draftCreatedAt'] = datetime.utcnow().isoformat()
                job['emailSent'] = False         # Will be updated by followup_service when detected
                job['gmailDraftId'] = draft_metadata.get('gmailDraftId')
                job['gmailThreadId'] = draft_metadata.get('gmailThreadId')
                # Follow-up defaults (can be overridden per job)
                if 'followUpDays' not in job:
                    job['followUpDays'] = 3
                if 'followUpSent' not in job:
                    job['followUpSent'] = False
                if 'lastFollowUpAt' not in job:
                    job['lastFollowUpAt'] = None
                if 'replyReceived' not in job:
                    job['replyReceived'] = False
                updated = True
                break

        if updated:
            save_jobs_to_json(jobs)
            logger.info(f"Saved draft metadata for job {job_id}: {draft_metadata}")
        return updated
    except Exception as e:
        logger.error(f"Error saving draft metadata for {job_id}: {e}")
        return False

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({
        "status": "ok",
        "service": "RecruitPulse API",
        "time": datetime.utcnow().isoformat(),
        "json_path": JSON_FILE_PATH
    }), 200

@app.route('/api/jobs', methods=['POST'])
def save_job():
    """
    Upsert job data into local jobs.json and MongoDB.
    Expects JSON payload with at least { jobId: "..." }
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            # If no data is sent, return 400 but don't error out
            return jsonify({"success": False, "error": "No JSON data provided or invalid JSON"}), 400

        job_id = data.get('jobId')
        if not job_id:
            return jsonify({"success": False, "error": "Missing jobId in payload"}), 400

        # Handle Telegram config update
        telegram_config = data.get('telegram_config')
        if telegram_config:
            save_telegram_config(telegram_config)

        # Timestamp
        if 'processedAt' not in data:
            data['processedAt'] = datetime.utcnow().isoformat()
            
        data['updatedAt'] = datetime.utcnow().isoformat()
        
        # Default emailSent to False if not present
        if 'emailSent' not in data:
            data['emailSent'] = False

        # 1. Update jobs.json
        jobs = load_jobs_from_json()
        found = False
        for i, existing_job in enumerate(jobs):
            if existing_job.get('jobId') == job_id:
                # [BUG FIX] Preserve existing sent status
                # If backend already marked it as sent, don't let the extension overwrite it to False
                if existing_job.get('emailSent') is True:
                    data['emailSent'] = True
                    # Carry over timestamps if missing in new data
                    if 'emailSentAt' not in data:
                        data['emailSentAt'] = existing_job.get('emailSentAt')
                
                # [BUG FIX] Preserve backend-managed fields that the extension doesn't send
                backend_fields = [
                    'gmailDraftId', 'gmailThreadId',
                    'draftCreated', 'draftCreatedAt',
                    'followUpDays', 'followUpSent', 'lastFollowUpAt', 'replyReceived',
                    'resumeId', 'resumeEditUrl',
                ]
                for field in backend_fields:
                    if field in existing_job and field not in data:
                        data[field] = existing_job[field]
                
                jobs[i] = data
                found = True
                break
        if not found:
            jobs.append(data)
        save_jobs_to_json(jobs)

        action = "updated" if found else "inserted"
        job_title = data.get('title', 'Unknown Title')
        print(f"✅ Job {action} (JSON: ok): {job_title} ({job_id})")

        return jsonify({
            "success": True,
            "message": f"Job {action} successfully into local storage",
            "jobId": job_id
        }), 200

    except Exception as e:
        print(f"❌ Error processing /api/jobs: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/jobs', methods=['GET'])
def get_all_jobs():
    """
    Returns all jobs from the local JSON file.
    """
    try:
        jobs = load_jobs_from_json()
        return jsonify(jobs), 200
    except Exception as e:
        print(f"❌ Error in GET /api/jobs: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/generate-resume-pdf', methods=['POST'])
def generate_resume_pdf():
    """
    Generates a PDF from HTML content provided in the request and sends an email with it.
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"success": False, "error": "No JSON data provided"}), 400

        html_content = data.get('resumeHtml')
        job_id = data.get('jobId', 'unknown')
        title = data.get('title', 'Resume')
        company = data.get('company', 'Company')
        resume_edit_url = data.get('resumeEditUrl')
        telegram_config = data.get('telegram_config')

        if telegram_config:
            save_telegram_config(telegram_config)

        if not html_content:
            return jsonify({"success": False, "error": "Missing resumeHtml in payload"}), 400

        # 1. Generate PDF
        filename = pdf_service.generate_pdf(html_content, job_id, title)
        if not filename:
            return jsonify({"success": False, "error": "Failed to generate PDF"}), 500

        pdf_path = os.path.join(PDF_OUTPUT_DIR, filename)
        
        # 2. Extract job details from jobs.json or payload
        # Find the job in jobs.json to get emailSubject and emailBody
        job_data = None
        try:
            jobs = load_jobs_from_json()
            for j in jobs:
                if j.get('jobId') == job_id:
                    job_data = j
                    break
        except Exception as e:
            logger.error(f"Error reading jobs.json: {e}")

        # If not found in JSON, use payload fallbacks
        if not job_data:
            job_data = {
                "jobId": job_id,
                "title": title,
                "company": company,
                "applyEmail": data.get('applyEmail', 'not-provided'),
                "emailSubject": data.get('emailSubject', f"Application for {title}"),
                "emailBody": data.get('emailBody', "Please find my resume attached.")
            }

        # 3. Create Gmail Draft with Attachment
        draft_created, draft_metadata = email_pipeline.send_email_with_attachment(job_data, pdf_path)
        
        # Extract enrichment data for Telegram (used by both success and failure)
        hm = job_data.get('hiringManager', {})
        hm_name = hm.get('name', 'N/A') if isinstance(hm, dict) else str(hm or 'N/A')
        hm_profile = hm.get('profileUrl', '') if isinstance(hm, dict) else ''
        job_url = job_data.get('jobPostUrl') or job_data.get('viewFullPostUrl', '')
        initial_msg = job_data.get('outreach', {}).get('initialMessage', '')
        followup_msg = job_data.get('outreach', {}).get('followUpMessage1', '')

        if draft_created:
            # 4. Update jobs.json with draft metadata + follow-up defaults
            _update_job_with_draft_metadata(job_id, draft_metadata)

            # 5. Send Telegram Notification (enriched, single consolidated message)

            telegram_lines = [
                f"📝 <b>Gmail Draft Created</b>",
                f"",
                f"🏢 <b>Company:</b> {company}",
                f"💼 <b>Role:</b> {title}",
                f"📧 <b>To:</b> {job_data.get('applyEmail')}",
                f"📎 <b>File:</b> {filename}",
                f"🆔 <b>Draft ID:</b> {draft_metadata.get('gmailDraftId', 'N/A')}",
            ]
            if resume_edit_url:
                telegram_lines.append(f"\n✏️ <b>Edit Resume:</b>\n{resume_edit_url}")
            if job_url:
                telegram_lines.append(f"\n🔗 <b>Job URL:</b>\n{job_url}")
            telegram_lines.append(f"\n👤 <b>Hiring Manager:</b>\n{hm_name}")
            if hm_profile:
                telegram_lines.append(f"{hm_profile}")
            if initial_msg:
                telegram_lines.append(f"\n📩 <b>Initial Message:</b>\n{initial_msg}")
            if followup_msg:
                telegram_lines.append(f"\n🔁 <b>Follow-up 1:</b>\n{followup_msg}")
            telegram_lines.append(f"\nReview and send from your Gmail Drafts.")

            telegram_msg = '\n'.join(telegram_lines)

            # Inline keyboard with Update Draft button
            reply_markup = json.dumps({
                "inline_keyboard": [
                    [
                        {
                            "text": "🔄 Update Draft",
                            "callback_data": f"update_draft:{job_id}"
                        }
                    ]
                ]
            })

            email_pipeline.send_telegram_notification(telegram_msg, pdf_path, reply_markup=reply_markup)

            return jsonify({
                "success": True, 
                "draftCreated": True,
                "filename": filename,
                "downloadUrl": f"{config.BASE_URL}/downloads/{filename}",
            }), 200
        else:
            # Draft failed — still send enriched details
            fail_lines = [
                f"❌ <b>No email provided in linkedin post</b>",
                f"",
                f"🏢 <b>Company:</b> {company}",
                f"💼 <b>Role:</b> {title}",
                f"📧 <b>To:</b> {job_data.get('applyEmail')}",
                f"📎 <b>Resume PDF:</b> {filename}",
            ]
            if job_url:
                fail_lines.append(f"\n🔗 <b>Job URL:</b>\n{job_url}")
            fail_lines.append(f"\n👤 <b>Hiring Manager:</b>\n{hm_name}")
            if hm_profile:
                fail_lines.append(f"{hm_profile}")
            if initial_msg:
                fail_lines.append(f"\n📩 <b>Initial Message:</b>\n{initial_msg}")
            if followup_msg:
                fail_lines.append(f"\n🔁 <b>Follow-up 1:</b>\n{followup_msg}")
            fail_lines.append(f"\n⚠️ PDF was generated but no draft was created.")
            email_pipeline.send_telegram_notification('\n'.join(fail_lines), pdf_path)
            return jsonify({
                "success": True, 
                "draftCreated": False, 
                "error": "Failed to create Gmail draft but PDF was generated",
                "filename": filename
            }), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/capture-resume-id', methods=['POST'])
def capture_resume_id():
    """
    Receives a resume ID and edit URL (fetched by the extension directly
    from the LandBetterJobs Supabase API). Persists resumeId and
    resumeEditUrl into jobs.json for the matching job.
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"success": False, "error": "No JSON data provided"}), 400

        resume_id = data.get('resume_id')
        edit_url = data.get('edit_url')
        job_id = data.get('job_id')

        if not resume_id or not edit_url:
            return jsonify({"success": False, "error": "Missing required fields: resume_id, edit_url"}), 400

        # Persist into jobs.json if job_id is provided
        if job_id:
            _update_job_field(job_id, 'resumeId', resume_id)
            _update_job_field(job_id, 'resumeEditUrl', edit_url)
            logger.info(f"Stored resumeId={resume_id} for job {job_id}")

        return jsonify({
            "success": True,
            "resume_id": resume_id,
            "edit_url": edit_url
        }), 200

    except Exception as e:
        logger.error(f"❌ Error in /api/capture-resume-id: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/job-by-resume/<resume_id>', methods=['GET'])
def get_job_by_resume(resume_id):
    """
    Looks up a job in jobs.json by its stored resumeId.
    Returns the job data if found.
    """
    try:
        jobs = load_jobs_from_json()
        for job in jobs:
            if job.get('resumeId') == resume_id:
                return jsonify({
                    "success": True,
                    "job": {
                        "jobId": job.get('jobId'),
                        "title": job.get('title'),
                        "company": job.get('company'),
                        "gmailDraftId": job.get('gmailDraftId'),
                        "resumeId": job.get('resumeId'),
                        "resumeEditUrl": job.get('resumeEditUrl'),
                    }
                }), 200
        return jsonify({"success": False, "error": "No job found for this resume_id"}), 404
    except Exception as e:
        logger.error(f"Error looking up job by resume_id {resume_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/test/reset-jobs', methods=['POST'])
def reset_jobs_for_testing():
    """
    Cleans jobs.json and pastes a predefined test job record.
    """
    try:
        test_jobs = []
        save_jobs_to_json(test_jobs)

        # Handle Telegram config update from query/headers if present
        # Note: popup 'Clean & Test' doesn't send payload by default, but we can check headers
        tg_config_header = request.headers.get('X-Telegram-Config')
        if tg_config_header:
            try:
                save_telegram_config(json.loads(tg_config_header))
            except:
                pass

        return jsonify({"success": True, "message": "Jobs reset for testing"}), 200
    except Exception as e:
        logger.error(f"❌ Error resetting jobs: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

def _update_job_field(job_id, field, value):
    """
    Updates a single field for a job in jobs.json.
    """
    try:
        jobs = load_jobs_from_json()
        for job in jobs:
            if job.get('jobId') == job_id:
                job[field] = value
                job['updatedAt'] = datetime.utcnow().isoformat()
                save_jobs_to_json(jobs)
                logger.info(f"Updated job {job_id}: {field} = {value}")
                return True
        return False
    except Exception as e:
        logger.error(f"Error updating field '{field}' for job {job_id}: {e}")
        return False


_update_draft_lock = {}

@app.route('/api/update-draft', methods=['POST'])
def update_draft():
    """
    Replaces an existing Gmail draft with an updated PDF.
    Called by extension (either from Export PDF click or pending action flow).

    Accepts either:
      - { job_id, resume_html }  → backend generates PDF via WeasyPrint (preferred)
      - { job_id, pdf_base64, pdf_filename? }  → pre-built PDF from extension
    """
    data = None
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"success": False, "error": "No JSON data provided"}), 400

        job_id = data.get('job_id')
        resume_html = data.get('resume_html')
        pdf_base64 = data.get('pdf_base64')

        if not job_id:
            return jsonify({"success": False, "error": "Missing required field: job_id"}), 400

        if not resume_html and not pdf_base64:
            return jsonify({"success": False, "error": "Missing resume_html or pdf_base64"}), 400

        # Check if this is from the Telegram pending action flow (lock already held)
        # or a direct Export PDF click (need to acquire lock)
        from_pending = False
        pending_actions = _load_pending_actions()
        if job_id in pending_actions:
            from_pending = True

        if not from_pending:
            if _update_draft_lock.get(job_id):
                return jsonify({"success": False, "error": "Update already in progress for this job"}), 409
            _update_draft_lock[job_id] = True

        try:
            # 1. Load job from jobs.json
            jobs = load_jobs_from_json()
            job_data = None
            for j in jobs:
                if j.get('jobId') == job_id:
                    job_data = j
                    break

            if not job_data:
                return jsonify({"success": False, "error": f"Job {job_id} not found in jobs.json"}), 404

            old_draft_id = job_data.get('gmailDraftId')
            gmail_thread_id = job_data.get('gmailThreadId')
            title = job_data.get('title', 'Resume')

            if not old_draft_id:
                logger.warning(f"No existing draft_id for job {job_id}. Will create a fresh draft.")

            # 2. Generate or decode PDF
            if resume_html:
                # Generate PDF from HTML using WeasyPrint (same as /api/generate-resume-pdf)
                pdf_filename = pdf_service.generate_pdf(resume_html, job_id, title)
                if not pdf_filename:
                    return jsonify({"success": False, "error": "Failed to generate PDF from HTML"}), 500
                pdf_path = os.path.join(PDF_OUTPUT_DIR, pdf_filename)
                logger.info(f"Generated updated PDF from HTML: {pdf_path}")
            else:
                # Decode pre-built PDF from base64
                pdf_bytes = b64.b64decode(pdf_base64)
                pdf_filename = data.get('pdf_filename', f"RecruitPulse_{job_id.replace(' ', '_')}_updated.pdf")
                pdf_path = os.path.join(PDF_OUTPUT_DIR, pdf_filename)
                with open(pdf_path, 'wb') as f:
                    f.write(pdf_bytes)
                logger.info(f"Saved updated PDF ({len(pdf_bytes)} bytes) to {pdf_path}")

            # 3. Delete old Gmail draft
            gmail_service = GmailService()
            if old_draft_id:
                delete_ok = gmail_service.delete_draft(old_draft_id)
                if delete_ok:
                    logger.info(f"Deleted old draft {old_draft_id}")
                else:
                    logger.warning(f"Could not delete old draft {old_draft_id}, proceeding anyway.")

            # 4. Create new draft with same email content + updated PDF
            to_email = job_data.get('applyEmail')
            subject = job_data.get('emailSubject')
            body = job_data.get('emailBody')

            success = False
            new_draft = None

            if gmail_thread_id:
                success, new_draft = gmail_service.create_draft_in_thread(
                    to_email, subject, body, pdf_path, gmail_thread_id
                )

            if not success:
                success, new_draft = gmail_service.create_draft(
                    to_email, subject, body, pdf_path
                )

            if not success:
                return jsonify({"success": False, "error": f"Failed to create new draft: {new_draft}"}), 500

            # 5. Update jobs.json with new draft metadata
            new_draft_id = new_draft.get('id')
            new_thread_id = new_draft.get('message', {}).get('threadId')
            _update_job_field(job_id, 'gmailDraftId', new_draft_id)
            if new_thread_id:
                _update_job_field(job_id, 'gmailThreadId', new_thread_id)

            # 6. Build Telegram confirmation
            company = job_data.get('company', 'Company')
            title = job_data.get('title', 'Role')
            resume_edit_url = job_data.get('resumeEditUrl', '')

            telegram_lines = [
                f"✅ <b>Draft Updated Successfully</b>",
                f"",
                f"🏢 <b>Company:</b> {company}",
                f"💼 <b>Role:</b> {title}",
                f"📧 <b>To:</b> {to_email}",
                f"📎 <b>Updated Resume:</b> {pdf_filename}",
                f"🆔 <b>New Draft ID:</b> {new_draft_id}",
            ]
            if resume_edit_url:
                telegram_lines.append(f"\n✏️ <b>Edit Resume:</b>\n{resume_edit_url}")
            telegram_lines.append(f"\nReview and send from your Gmail Drafts.")

            telegram_msg = '\n'.join(telegram_lines)

            # Include Update Draft button for subsequent updates
            update_markup = json.dumps({
                "inline_keyboard": [
                    [
                        {
                            "text": "🔄 Update Draft",
                            "callback_data": f"update_draft:{job_id}"
                        }
                    ]
                ]
            })

            # 7. Send Telegram confirmation
            # If triggered via pending action, send directly to the chat_id
            chat_id = None
            pending_actions = _load_pending_actions()
            action = pending_actions.get(job_id)
            if action:
                chat_id = action.get('chat_id')

            if chat_id:
                bot_token, _ = _get_telegram_credentials()
                if bot_token:
                    _send_telegram_direct(bot_token, chat_id, telegram_msg,
                                          document_path=pdf_path, reply_markup=update_markup)
            else:
                email_pipeline.send_telegram_notification(
                    telegram_msg, pdf_path, reply_markup=update_markup)

            # 8. Clear pending action
            _clear_pending_action(job_id)

            return jsonify({
                "success": True,
                "newDraftId": new_draft_id,
                "updatedPdf": pdf_filename,
            }), 200

        finally:
            _update_draft_lock.pop(job_id, None)

    except Exception as e:
        logger.error(f"❌ Error in /api/update-draft: {e}")
        job_id = data.get('job_id') if data else None
        if job_id:
            _update_draft_lock.pop(job_id, None)
            _clear_pending_action(job_id)
        return jsonify({"success": False, "error": str(e)}), 500


# ─── Telegram Webhook: Inline Button Callbacks ───────────────────────────────

def _get_telegram_credentials():
    """
    Returns (bot_token, chat_ids) from dynamic config file or static config.
    """
    bot_token = config.TELEGRAM_BOT_TOKEN
    chat_ids = config.TELEGRAM_CHAT_IDS

    tg_config_path = os.path.join(BASE_DIR, 'telegram_config.json')
    if os.path.exists(tg_config_path):
        try:
            with open(tg_config_path, 'r') as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                    if data.get("bot_token"):
                        bot_token = data["bot_token"]
                    if data.get("chat_ids"):
                        chat_ids = data["chat_ids"]
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except Exception as e:
            logger.error(f"Error loading telegram config: {e}")

    return bot_token, chat_ids


def _answer_callback_query(bot_token, callback_query_id, text="Processing..."):
    """Acknowledge a Telegram inline button press."""
    try:
        http_requests.post(
            f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=5
        )
    except Exception as e:
        logger.error(f"Failed to answer callback query: {e}")


def _send_telegram_direct(bot_token, chat_id, message, document_path=None, reply_markup=None):
    """
    Send a Telegram message (and optional document) directly.
    Used from webhook context where we already know the chat_id.
    """
    try:
        msg_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        http_requests.post(msg_url, json=payload, timeout=10).raise_for_status()

        if document_path and os.path.exists(document_path):
            doc_url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
            with open(document_path, 'rb') as doc:
                files = {'document': doc}
                doc_data = {"chat_id": chat_id, "caption": "📎 Updated Resume PDF", "parse_mode": "HTML"}
                http_requests.post(doc_url, data=doc_data, files=files, timeout=20).raise_for_status()

        return True
    except Exception as e:
        logger.error(f"Failed to send Telegram message to {chat_id}: {e}")
        return False


# ─── Pending Actions Store (File-Based, VM-Safe) ─────────────────────────────
# State machine: pending → processing → completed | expired | failed
# Stored in pending_actions.json for restart/VM separation safety.

PENDING_ACTIONS_FILE = os.path.join(BASE_DIR, 'pending_actions.json')


def _load_pending_actions():
    """Load pending actions from JSON file."""
    if not os.path.exists(PENDING_ACTIONS_FILE):
        return {}
    try:
        with open(PENDING_ACTIONS_FILE, 'r') as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                data = json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Error loading pending_actions.json: {e}")
        return {}


def _save_pending_actions(actions):
    """Save pending actions to JSON file."""
    try:
        with open(PENDING_ACTIONS_FILE, 'w') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(actions, f, indent=2)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except IOError as e:
        logger.error(f"Error saving pending_actions.json: {e}")


def _create_pending_action(job_id, resume_edit_url, chat_id=None):
    """Store a pending action for the extension to pick up (file-based)."""
    actions = _load_pending_actions()
    if job_id in actions and actions[job_id].get('status') == 'processing':
        return False  # Already being processed
    actions[job_id] = {
        'action': 'UPDATE_RESUME_PDF',
        'job_id': job_id,
        'resumeEditUrl': resume_edit_url,
        'status': 'pending',
        'chat_id': chat_id,
        'created_at': time.time(),
    }
    _save_pending_actions(actions)
    logger.info(f"Created pending action for job {job_id}")
    return True


def _get_pending_action():
    """Return the first pending action (FIFO) or None."""
    actions = _load_pending_actions()
    for job_id, action in actions.items():
        if action.get('status') == 'pending':
            return action
    return None


def _update_pending_action_status(job_id, status):
    """Update status of a pending action (file-based)."""
    actions = _load_pending_actions()
    if job_id in actions:
        actions[job_id]['status'] = status
        _save_pending_actions(actions)
        logger.info(f"Pending action {job_id} → {status}")


def _clear_pending_action(job_id):
    """Remove a completed/failed pending action (file-based)."""
    actions = _load_pending_actions()
    if job_id in actions:
        del actions[job_id]
        _save_pending_actions(actions)


@app.route('/api/pending-actions', methods=['GET'])
def get_pending_actions():
    """
    Returns the next pending action for the extension to process.
    Marks it as 'processing' once fetched.
    Expires actions older than 300 seconds.
    """
    actions = _load_pending_actions()
    changed = False

    # Expire stale actions (> 300 seconds old)
    for job_id, action in list(actions.items()):
        created_at = action.get('created_at', 0)
        if action.get('status') in ('pending', 'processing') and time.time() - created_at > 300:
            action['status'] = 'expired'
            changed = True
            logger.warning(f"Pending action expired for job {job_id}")

    if changed:
        _save_pending_actions(actions)

    # Find first pending action
    for job_id, action in actions.items():
        if action.get('status') == 'pending':
            action['status'] = 'processing'
            _save_pending_actions(actions)
            return jsonify({
                "success": True,
                "action": {
                    "job_id": action['job_id'],
                    "action": action['action'],
                    "resumeEditUrl": action['resumeEditUrl'],
                }
            }), 200

    return jsonify({"success": True, "action": None}), 200


@app.route('/api/complete-action', methods=['POST'])
def complete_action():
    """
    Called by the extension to report action completion or failure.
    Expects JSON: { job_id, status: 'completed'|'failed', error? }
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"success": False, "error": "No JSON data"}), 400

        job_id = data.get('job_id')
        status = data.get('status', 'failed')
        error = data.get('error')

        if not job_id:
            return jsonify({"success": False, "error": "Missing job_id"}), 400

        pending_actions = _load_pending_actions()
        action = pending_actions.get(job_id)

        if status == 'failed' and action:
            # Notify Telegram about the failure
            chat_id = action.get('chat_id')
            if chat_id:
                bot_token, _ = _get_telegram_credentials()
                if bot_token:
                    _send_telegram_direct(bot_token, chat_id,
                        f"❌ <b>Draft update failed</b> for job <code>{job_id}</code>\n"
                        f"Error: {error or 'Unknown error'}\n\n"
                        f"Make sure the extension is running and you are logged in to LandBetterJobs.")

        _clear_pending_action(job_id)
        _update_draft_lock.pop(job_id, None)

        return jsonify({"success": True, "status": status}), 200

    except Exception as e:
        logger.error(f"Error in /api/complete-action: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _handle_telegram_update_draft(job_id, bot_token, chat_id):
    """
    Triggered by Telegram inline button. Creates a pending action for the
    extension to pick up. Does NOT process the draft directly.
    """
    # 1. Load job to get resumeEditUrl
    jobs = load_jobs_from_json()
    job_data = None
    for j in jobs:
        if j.get('jobId') == job_id:
            job_data = j
            break

    if not job_data:
        _send_telegram_direct(bot_token, chat_id,
            f"❌ Job <code>{job_id}</code> not found.")
        return

    resume_edit_url = job_data.get('resumeEditUrl')
    if not resume_edit_url:
        _send_telegram_direct(bot_token, chat_id,
            f"❌ No resume edit URL stored for job <code>{job_id}</code>.")
        return

    company = job_data.get('company', 'Company')
    title = job_data.get('title', 'Role')

    # 2. Check for duplicate
    if _update_draft_lock.get(job_id):
        _send_telegram_direct(bot_token, chat_id,
            f"⏳ Update already in progress for:\n🏢 {company} — 💼 {title}")
        return
    _update_draft_lock[job_id] = True

    # 3. Create pending action for extension
    created = _create_pending_action(job_id, resume_edit_url, chat_id)
    if not created:
        _update_draft_lock.pop(job_id, None)
        _send_telegram_direct(bot_token, chat_id,
            f"⏳ Update already in progress for:\n🏢 {company} — 💼 {title}")
        return

    # 4. Notify user
    _send_telegram_direct(bot_token, chat_id,
        f"🔄 <b>Update Draft requested</b>\n"
        f"🏢 {company} — 💼 {title}\n\n"
        f"⏳ Waiting for extension to download updated resume…\n"
        f"Make sure the extension is running.")

    logger.info(f"Pending action created for job {job_id}, waiting for extension.")
    # No threading timeout — extension drives lifecycle.
    # Expiry is handled by /api/pending-actions (300s TTL).


@app.route('/telegram/webhook', methods=['POST'])
def telegram_webhook():
    """
    Receives updates from Telegram Bot API (webhook mode).
    Handles inline keyboard callback queries (e.g. Update Draft button).
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"ok": True}), 200

        bot_token, _ = _get_telegram_credentials()

        if "callback_query" in data:
            callback = data["callback_query"]
            callback_query_id = callback.get("id")
            callback_data = callback.get("data", "")
            chat_id = callback.get("message", {}).get("chat", {}).get("id")

            _answer_callback_query(bot_token, callback_query_id,
                                   text="⏳ Requesting extension to update draft...")

            if callback_data.startswith("update_draft:"):
                job_id = callback_data.split(":", 1)[1]
                logger.info(f"Telegram callback: update_draft for job {job_id}")

                if chat_id:
                    threading.Thread(
                        target=_handle_telegram_update_draft,
                        args=(job_id, bot_token, chat_id),
                        daemon=True
                    ).start()
            else:
                logger.warning(f"Unknown callback_data: {callback_data}")

        return jsonify({"ok": True}), 200

    except Exception as e:
        logger.error(f"❌ Error in /telegram/webhook: {e}")
        return jsonify({"ok": True}), 200


@app.route('/api/send-pending-emails', methods=['POST'])
def send_pending_emails():
    """DEPRECATED: Use /api/generate-resume-pdf for integrated PDF + Email flow."""
    return jsonify({
        "success": False,
        "message": "This endpoint is deprecated. Use /api/generate-resume-pdf for integrated flow."
    }), 410

@app.route('/downloads/<filename>', methods=['GET'])
def download_file(filename):
    """
    Serves a file from the generated_pdfs directory.
    """
    try:
        return send_from_directory(PDF_OUTPUT_DIR, filename, as_attachment=True)
    except Exception as e:
        print(f"❌ Error serving file {filename}: {e}")
        return jsonify({"success": False, "error": "File not found"}), 404

# ─── Telegram Long-Polling (no ngrok required) ───────────────────────────────

_telegram_polling_active = False

def _start_telegram_polling():
    """
    Starts a background thread that long-polls Telegram for updates.
    Routes callback_query to pending action creation (extension-assisted).
    """
    global _telegram_polling_active

    bot_token, _ = _get_telegram_credentials()
    if not bot_token:
        logger.warning("No Telegram bot token configured. Polling not started.")
        return

    _telegram_polling_active = True
    offset = None

    logger.info("🤖 Telegram polling started.")

    while _telegram_polling_active:
        try:
            bot_token, _ = _get_telegram_credentials()
            if not bot_token:
                time.sleep(10)
                continue

            url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
            params = {"timeout": 30, "allowed_updates": ["callback_query"]}
            if offset:
                params["offset"] = offset

            resp = http_requests.get(url, params=params, timeout=35)
            data = resp.json()

            if not data.get("ok"):
                logger.error(f"Telegram getUpdates error: {data}")
                time.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                if "callback_query" in update:
                    callback = update["callback_query"]
                    callback_query_id = callback.get("id")
                    callback_data = callback.get("data", "")
                    chat_id = callback.get("message", {}).get("chat", {}).get("id")

                    _answer_callback_query(bot_token, callback_query_id,
                                           text="⏳ Requesting extension to update draft...")

                    if callback_data.startswith("update_draft:"):
                        job_id = callback_data.split(":", 1)[1]
                        logger.info(f"Telegram poll: update_draft for job {job_id}")

                        if chat_id:
                            threading.Thread(
                                target=_handle_telegram_update_draft,
                                args=(job_id, bot_token, chat_id),
                                daemon=True
                            ).start()
                    else:
                        logger.warning(f"Unknown callback_data from polling: {callback_data}")

        except http_requests.exceptions.Timeout:
            continue
        except Exception as e:
            logger.error(f"Telegram polling error: {e}")
            time.sleep(5)


if __name__ == '__main__':
    port = PORT
    print(f"🚀 Server running on {config.BASE_URL}")
    print(f"📂 Data will be saved to: {JSON_FILE_PATH}")

    # Start Telegram polling in background (only in main process, not reloader)
    if not os.environ.get('WERKZEUG_RUN_MAIN'):
        pass  # Skip in reloader parent process
    else:
        poller = threading.Thread(target=_start_telegram_polling, daemon=True)
        poller.start()
        print("🤖 Telegram inline button polling active.")

    app.run(host='0.0.0.0', port=port, debug=True)
