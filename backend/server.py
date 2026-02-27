from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import json
import requests as http_requests
from datetime import datetime
import fcntl
import logging
from job_email_service import JobEmailService
from pdf_service import PdfService
import email_pipeline
import config
import google_docs_service
from gmail_service import GmailService
import scheduler  # Starts APScheduler background job on import

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
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
                    'gmailDraftId', 'gmailThreadId', 'googleDocId',
                    'draftCreated', 'draftCreatedAt',
                    'followUpDays', 'followUpSent', 'lastFollowUpAt', 'replyReceived',
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

            # 5. Create Google Doc version of the resume
            google_doc_id = None
            google_doc_url = None
            try:
                # Extract clean plain text from HTML for the Google Doc
                import re
                clean_html = html_content
                # 1. Remove <style>...</style> and <script>...</script> blocks entirely
                clean_html = re.sub(r'<style[^>]*>.*?</style>', '', clean_html, flags=re.DOTALL | re.IGNORECASE)
                clean_html = re.sub(r'<script[^>]*>.*?</script>', '', clean_html, flags=re.DOTALL | re.IGNORECASE)
                # 2. Replace <br>, <br/>, </p>, </div>, </li>, </h1-6> with newlines
                clean_html = re.sub(r'<br\s*/?>', '\n', clean_html, flags=re.IGNORECASE)
                clean_html = re.sub(r'</(?:p|div|li|h[1-6]|tr)>', '\n', clean_html, flags=re.IGNORECASE)
                # 3. Replace bullet list items with a bullet character
                clean_html = re.sub(r'<li[^>]*>', '• ', clean_html, flags=re.IGNORECASE)
                # 4. Strip remaining HTML tags
                resume_plain_text = re.sub(r'<[^>]+>', '', clean_html)
                # 5. Clean up whitespace: collapse multiple blank lines, trim lines
                lines = [line.strip() for line in resume_plain_text.splitlines()]
                resume_plain_text = '\n'.join(lines)
                resume_plain_text = re.sub(r'\n{3,}', '\n\n', resume_plain_text)
                resume_plain_text = resume_plain_text.strip()

                google_doc_id = google_docs_service.create_resume_doc(job_id, resume_plain_text, title_prefix=f"{title} - {company}")
                if google_doc_id:
                    google_docs_service.share_doc_with_anyone(google_doc_id)
                    google_doc_url = google_docs_service.get_edit_url(google_doc_id)
                    # Save googleDocId to jobs.json
                    _update_job_field(job_id, 'googleDocId', google_doc_id)
                    logger.info(f"Google Doc created for job {job_id}: {google_doc_url}")
                else:
                    logger.warning(f"Google Doc creation returned None for job {job_id}")
            except Exception as e:
                logger.error(f"Google Doc creation failed for job {job_id}: {e}")

            # 6. Send Telegram Notification (enriched)

            telegram_lines = [
                f"📝 <b>Gmail Draft Created</b>",
                f"",
                f"🏢 <b>Company:</b> {company}",
                f"💼 <b>Role:</b> {title}",
                f"📧 <b>To:</b> {job_data.get('applyEmail')}",
                f"📎 <b>File:</b> {filename}",
                f"🆔 <b>Draft ID:</b> {draft_metadata.get('gmailDraftId', 'N/A')}",
            ]
            if google_doc_url:
                telegram_lines.append(f"\n✏️ <b>Edit Resume:</b>\n{google_doc_url}")
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

            # Build inline keyboard with "Update Draft" button
            reply_markup = None
            if google_doc_url:
                reply_markup = {
                    "inline_keyboard": [
                        [
                            {
                                "text": "🔄 Update Draft",
                                "callback_data": f"update_resume:{job_id}"
                            }
                        ]
                    ]
                }

            email_pipeline.send_telegram_notification(telegram_msg, pdf_path, reply_markup=reply_markup)

            return jsonify({
                "success": True, 
                "draftCreated": True,
                "filename": filename,
                "downloadUrl": f"{config.BASE_URL}/downloads/{filename}",
                "googleDocUrl": google_doc_url,
            }), 200
        else:
            # Draft failed — still send enriched details
            fail_lines = [
                f"❌ <b>Gmail Draft Failed</b>",
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
            fail_lines.append(f"\n⚠️ PDF was generated but draft creation failed.")
            email_pipeline.send_telegram_notification('\n'.join(fail_lines), pdf_path)
            return jsonify({
                "success": True, 
                "draftCreated": False, 
                "error": "Failed to create Gmail draft but PDF was generated",
                "filename": filename
            }), 200

    except Exception as e:
        logger.error(f"❌ Error in /api/generate-resume-pdf: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/test/reset-jobs', methods=['POST'])
def reset_jobs_for_testing():
    """
    Cleans jobs.json and pastes a predefined test job record.
    """
    try:
        test_jobs = [
             {
            "applyEmail": "rajat.srivastava@algofolks.com",
            "company": "Frido",
            "emailBody": "Dear Shubham Somalkar,\n\nMy name is Gautham Madhu, and I am a Product Manager at Axis Bank with 5-10 years of experience in leadership, risk management, and optimizing digital performance metrics. I am writing to express my strong interest in the Manager \u2013 Costing (CMA) position at Frido, which I believe aligns perfectly with my professional background and career aspirations.\n\nDuring my tenure at Axis Bank, I've gained significant experience in understanding financial operations, managing complex projects, and utilizing data to drive strategic decisions. Prior to this, as a Team Lead at J. Edgerton Consulting for seven years, and a Resolutions Team Lead at Mount Rose Technologies, I honed my skills in staff management, effective time management, and fostering high-performing teams, which are crucial for a managerial role at Frido.\n\nI am particularly adept at risk management and understanding digital performance metrics, skills that are directly transferable to optimizing costing strategies and ensuring financial efficiency within your organization. My ability to communicate effectively in English, Spanish, Polish, and French also provides a valuable asset for a company with diverse operations or international collaborations.\n\nI am confident that my leadership capabilities, coupled with my analytical mindset and commitment to driving results, make me a strong candidate for this role. I am eager to learn more about Frido's mission and discuss how my skills and experience can contribute to your team's success.\n\nWould you be available for a brief call next week to explore this opportunity further?\n\nThank you for your time and consideration.\n\nSincerely,\nGautham Madhu\ngauthammadhu27@gmail.com",
            "emailSent": False,
            "emailSubject": "Experienced PM (Axis Bank) interested in Manager \u2013 Costing (CMA) at Frido - Gautham Madhu",
            "experience": "",
            "fullDescription": "Make the most of your professional life\nEmail or phone number\nPassword\nShow\nRemember me\n\nAgree & Join LinkedIn\n\nBy clicking Continue, you agree to LinkedIn\u2019s User Agreement, Privacy Policy, and Cookie Policy.\n\nBy clicking Agree & Join or Continue, you agree to the LinkedIn User Agreement, Privacy Policy, and Cookie Policy.\n\nAgree & Join\nor\nContinue with Google\n\nAlready on LinkedIn? Sign in\n\nLooking to create a page for a business? Get help\n\nLinkedIn\n\u00a9 2026\nAbout\nAccessibility\nUser Agreement\nPrivacy Policy\nCookie Policy\nCopyright Policy\nBrand Policy\nGuest Controls\nCommunity Guidelines\nLanguage",
            "hiringManager": {
                "name": "Shubham Somalkar",
                "profileUrl": "https://www.linkedin.com/in/shubham-somalkar-76b04016b?miniProfileUrn=urn%3Ali%3Afsd_profile%3AACoAAChn-EMBK6V6rG3EutZVMuEzojEHKT8CXKk"
            },
            "jdResumeBuilt": False,
            "jobId": "job_993125625",
            "jobPostUrl": "https://www.linkedin.com/feed/update/urn:li:activity:7430877585212903425",
            "location": "",
            "outreach": {
                "followUpMessage1": "Hi Shubham, following up on my interest in the Manager \u2013 Costing (CMA) role. My experience as a Team Lead at J. Edgerton Consulting, where I managed teams and improved processes, directly translates to the leadership and strategic costing responsibilities at Frido. I'm confident in my ability to drive efficiency.",
                "initialMessage": "Hello Shubham, I'm Gautham Madhu, a Product Manager at Axis Bank with 5-10 years experience, and I'm very interested in the Manager \u2013 Costing (CMA) role at Frido. My leadership, risk management, and digital performance metrics skills, honed at Axis Bank and J. Edgerton Consulting, align strongly with the requirements. I'd love to connect and discuss how I can contribute to Frido's success."
            },
            "processedAt": "2026-02-25T13:01:16.259Z",
            "shortDescription": "5\u201310 years | Manufacturing / Product-based industry preferredStrong exposure to product costing, inventory, MIS & margin analysis required.Interested candidates can share their resume at:shubham.somalkar@myfrido.com or ari...",
            "source": "linkedin",
            "telegram_config": {
                "botToken": "8653643537:AAH4kaIH-mEQIB_hZ-FWPuM3B-eyUWrtYsc",
                "chatIds": "5770045910,5770045911"
            },
            "title": "Manager \u2013 Costing (CMA)",
            "updatedAt": "2026-02-25T13:02:05.549878",
            "viewFullPostUrl": "https://www.linkedin.com/feed/update/urn:li:activity:7430877585212903425",
            "jdResumeBuiltAt": "2026-02-25T13:02:05.544Z"
        }
        ]
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


def _do_resume_update(job_id):
    """
    Core logic: exports Google Doc as PDF, replaces Gmail draft.
    Returns (success: bool, result: dict).
    Called by both the HTTP endpoint and the Telegram webhook.
    """
    # 1. Load job from jobs.json
    jobs = load_jobs_from_json()
    job_data = None
    for j in jobs:
        if j.get('jobId') == job_id:
            job_data = j
            break

    if not job_data:
        return False, {"error": f"Job {job_id} not found"}

    google_doc_id = job_data.get('googleDocId')
    gmail_draft_id = job_data.get('gmailDraftId')
    gmail_thread_id = job_data.get('gmailThreadId')

    if not google_doc_id:
        return False, {"error": "No Google Doc found for this job. Resume must be generated first."}

    if not gmail_draft_id or not gmail_thread_id:
        return False, {"error": "No Gmail draft metadata found for this job."}

    # 2. Check if email was already sent
    gmail_service = GmailService()
    if gmail_service.was_email_sent(gmail_thread_id):
        return False, {"error": "Email has already been sent. Cannot update a sent email."}

    # 3. Export Google Doc as PDF
    updated_pdf_filename = f"RecruitPulse_{job_id.replace(' ', '_')}_updated.pdf"
    updated_pdf_path = os.path.join(PDF_OUTPUT_DIR, updated_pdf_filename)

    export_success = google_docs_service.export_doc_as_pdf(google_doc_id, updated_pdf_path)
    if not export_success:
        return False, {"error": "Failed to export Google Doc as PDF"}

    # 4. Delete old Gmail draft
    delete_success = gmail_service.delete_draft(gmail_draft_id)
    if not delete_success:
        logger.warning(f"Could not delete old draft {gmail_draft_id}, proceeding anyway.")

    # 5. Create new draft (try same thread first, fall back to new thread)
    to_email = job_data.get('applyEmail')
    subject = job_data.get('emailSubject')
    body = job_data.get('emailBody')

    success, new_draft = gmail_service.create_draft_in_thread(
        to_email, subject, body, updated_pdf_path, gmail_thread_id
    )
    if not success:
        logger.warning(f"Could not create draft in thread {gmail_thread_id}, creating fresh draft instead.")
        success, new_draft = gmail_service.create_draft(
            to_email, subject, body, updated_pdf_path
        )

    if not success:
        return False, {"error": f"Failed to create new draft: {new_draft}"}

    # 6. Update jobs.json with new draft ID and thread ID
    new_draft_id = new_draft.get('id')
    new_thread_id = new_draft.get('message', {}).get('threadId')
    _update_job_field(job_id, 'gmailDraftId', new_draft_id)
    if new_thread_id:
        _update_job_field(job_id, 'gmailThreadId', new_thread_id)

    # 7. Send Telegram confirmation with updated PDF attached
    company = job_data.get('company', 'Company')
    title = job_data.get('title', 'Role')
    doc_url = google_docs_service.get_edit_url(google_doc_id)

    confirm_lines = [
        f"🔄 <b>Resume Draft Updated</b>",
        f"",
        f"🏢 <b>Company:</b> {company}",
        f"💼 <b>Role:</b> {title}",
        f"📧 <b>To:</b> {to_email}",
        f"📎 <b>Updated PDF:</b> {updated_pdf_filename}",
        f"🆔 <b>New Draft ID:</b> {new_draft_id}",
        f"\n✏️ <b>Google Doc:</b>\n{doc_url}",
        f"\n✅ Old draft deleted and replaced with updated resume.",
    ]
    confirm_msg = '\n'.join(confirm_lines)
    email_pipeline.send_telegram_notification(confirm_msg, updated_pdf_path)

    return True, {
        "message": "Resume draft updated successfully",
        "newDraftId": new_draft_id,
        "updatedPdf": updated_pdf_filename,
    }


@app.route('/api/update-resume/<job_id>', methods=['POST'])
def update_resume(job_id):
    """
    HTTP endpoint: exports Google Doc as PDF, replaces Gmail draft.
    """
    try:
        success, result = _do_resume_update(job_id)
        if success:
            return jsonify({"success": True, **result}), 200
        else:
            return jsonify({"success": False, **result}), 400
    except Exception as e:
        logger.error(f"❌ Error in /api/update-resume/{job_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/telegram/webhook', methods=['POST'])
def telegram_webhook():
    """
    Receives Telegram callback queries from inline buttons.
    Handles 'update_resume:<jobId>' callback data.
    Improved UX: popup ack → status msg → process → edit status → remove button.
    """
    try:
        update = request.get_json(silent=True)
        if not update:
            return 'OK', 200

        callback_query = update.get('callback_query')
        if not callback_query:
            return 'OK', 200

        callback_id = callback_query.get('id')
        callback_data = callback_query.get('data', '')
        chat_id = callback_query.get('message', {}).get('chat', {}).get('id')
        original_message_id = callback_query.get('message', {}).get('message_id')

        bot_token = config.TELEGRAM_BOT_TOKEN
        api_base = f"https://api.telegram.org/bot{bot_token}"

        # 1. Acknowledge callback with popup toast
        http_requests.post(
            f"{api_base}/answerCallbackQuery",
            json={
                "callback_query_id": callback_id,
                "text": "⏳ Updating draft... please wait",
                "show_alert": False,
            }
        )

        if callback_data.startswith('update_resume:'):
            job_id = callback_data.split(':', 1)[1]
            logger.info(f"Telegram callback: update_resume for job {job_id}")

            # 2. Send a temporary status message
            status_resp = http_requests.post(
                f"{api_base}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "⏳ <b>Updating resume draft...</b>\n\nExporting Google Doc → PDF → Replacing Gmail draft...",
                    "parse_mode": "HTML",
                }
            ).json()
            status_msg_id = status_resp.get('result', {}).get('message_id')

            # 3. Remove the inline button from original message (prevent duplicate clicks)
            original_text = callback_query.get('message', {}).get('text', '')
            http_requests.post(
                f"{api_base}/editMessageReplyMarkup",
                json={
                    "chat_id": chat_id,
                    "message_id": original_message_id,
                    "reply_markup": {"inline_keyboard": []},
                }
            )

            # 4. Process the resume update
            success, result = _do_resume_update(job_id)

            # 5. Edit the status message with the result
            if success:
                final_msg = (
                    f"✅ <b>Resume Draft Updated Successfully!</b>\n\n"
                    f"📎 New PDF: {result.get('updatedPdf', 'N/A')}\n"
                    f"🆔 New Draft ID: {result.get('newDraftId', 'N/A')}\n\n"
                    f"Review and send from your Gmail Drafts."
                )
            else:
                error_text = result.get('error', 'Unknown error')
                final_msg = f"❌ <b>Update Failed</b>\n\n{error_text}"

            if status_msg_id:
                http_requests.post(
                    f"{api_base}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": status_msg_id,
                        "text": final_msg,
                        "parse_mode": "HTML",
                    }
                )
            else:
                # Fallback: send as new message if edit fails
                http_requests.post(
                    f"{api_base}/sendMessage",
                    json={"chat_id": chat_id, "text": final_msg, "parse_mode": "HTML"}
                )

        return 'OK', 200

    except Exception as e:
        logger.error(f"❌ Error in /telegram/webhook: {e}")
        return 'OK', 200  # Always return 200 to Telegram


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

if __name__ == '__main__':
    port = PORT
    print(f"🚀 Server running on {config.BASE_URL}")
    print(f"📂 Data will be saved to: {JSON_FILE_PATH}")
    app.run(host='0.0.0.0', port=port, debug=True)
