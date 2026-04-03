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
from dotenv import load_dotenv
from job_email_service import JobEmailService
from pdf_service import PdfService
import email_pipeline
import config
import llm_service
from gmail_service import GmailService
import scheduler  # Starts APScheduler background job on import
from db import mongodb

# Load environment variables
load_dotenv(override=True)

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

def refresh_rag_token():
    """
    Calls the login API to get a new JWT token for the RAG service.
    Updates the environment variable and persists it to the .env file.
    """
    try:
        login_url = "https://be-ext.algofolks.com/api/auth/login"
        email = os.getenv('RAG_LOGIN_EMAIL', 'test@gmail.com')
        password = os.getenv('RAG_LOGIN_PASSWORD', 'Test@123')
        
        logger.info(f"Refreshing RAG token for {email}...")
        
        response = http_requests.post(
            login_url, 
            json={"email": email, "password": password},
            timeout=15
        )
        
        if response.status_code == 200:
            data = response.json()
            new_token = data.get('token')
            if not new_token:
                logger.error("Login successful but no token found in response")
                return None
            
            # Update current process environment
            os.environ['RAG_AUTH_TOKEN'] = new_token
            
            # Persist to .env file
            # Try to find BASE_DIR from the module or use current directory
            base_dir = globals().get('BASE_DIR', os.path.dirname(os.path.abspath(__file__)))
            env_path = os.path.join(base_dir, '.env')
            if os.path.exists(env_path):
                try:
                    with open(env_path, 'r') as f:
                        lines = f.readlines()
                    
                    with open(env_path, 'w') as f:
                        token_updated = False
                        for line in lines:
                            if line.startswith('RAG_AUTH_TOKEN='):
                                f.write(f"RAG_AUTH_TOKEN={new_token}\n")
                                token_updated = True
                            else:
                                f.write(line)
                        if not token_updated:
                            f.write(f"RAG_AUTH_TOKEN={new_token}\n")
                    logger.info("Successfully persisted new RAG token to .env")
                except Exception as e:
                    logger.error(f"Failed to update .env with new token: {e}")
                    
            return new_token
        else:
            logger.error(f"RAG login failed: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error refreshing RAG token: {e}")
        return None

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({
        "status": "ok",
        "service": "RecruitPulse API",
        "time": datetime.utcnow().isoformat(),
        "json_path": JSON_FILE_PATH
    }), 200

@app.route('/api/llm-config', methods=['GET'])
def get_llm_config():
    """Returns current LLM settings from configuration."""
    try:
        return jsonify({
            "anthropicKey": os.getenv("ANTHROPIC_API_KEY", ""),
            "anthropicModel": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            "groqKey": os.getenv("GROQ_API_KEY", ""),
            "groqModel": os.getenv("GROQ_MODEL", "llama3-70b-8192")
        }), 200
    except Exception as e:
        logger.error(f"Error fetching LLM config: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/llm-config', methods=['POST'])
def update_llm_config():
    """Updates LLM configuration in .env and reloads it."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        # Map frontend keys to .env keys
        mappings = {
            "anthropicKey": "ANTHROPIC_API_KEY",
            "anthropicModel": "ANTHROPIC_MODEL",
            "groqKey": "GROQ_API_KEY",
            "groqModel": "GROQ_MODEL"
        }

        env_path = os.path.join(BASE_DIR, '.env')
        # Read existing .env
        env_lines = []
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                env_lines = f.readlines()

        # Update or add lines
        new_env_content = []
        applied_keys = set()

        for line in env_lines:
            found_mapping = False
            for fe_key, env_key in mappings.items():
                if line.startswith(f"{env_key}="):
                    if fe_key in data:
                        new_env_content.append(f"{env_key}={data[fe_key]}\n")
                        applied_keys.add(env_key)
                    else:
                        new_env_content.append(line)
                    found_mapping = True
                    break
            if not found_mapping:
                new_env_content.append(line)

        # Add any keys that weren't in the .env originally
        for fe_key, env_key in mappings.items():
            if fe_key in data and env_key not in applied_keys:
                new_env_content.append(f"{env_key}={data[fe_key]}\n")

        # Write back to .env
        with open(env_path, 'w') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.writelines(new_env_content)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

        # Reload environment and refresh config module
        load_dotenv(env_path, override=True)
        import importlib
        importlib.reload(config)
        importlib.reload(email_pipeline)
        importlib.reload(llm_service)

        logger.info("LLM configuration updated and reloaded successfully.")
        return jsonify({"success": True, "message": "Configuration updated successfully"}), 200

    except Exception as e:
        logger.error(f"Error updating LLM config: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

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

@app.route('/api/interview-prep', methods=['POST'])
def save_interview_prep():
    """
    Save interview preparation data to Vector DB (RAG).
    Converts JSON to markdown and uploads to RAG endpoint.
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"success": False, "error": "No JSON data provided"}), 400
        
        job_id = data.get('jobId')
        position = data.get('position', 'Unknown Position')
        company = data.get('company', 'Unknown Company')
        scraped_data = data.get('scrapedData', {})
        
        if not job_id:
            return jsonify({"success": False, "error": "Missing jobId in payload"}), 400
        
        # Format data as markdown
        markdown_content = format_interview_prep_as_markdown(
            job_id, position, company, scraped_data
        )
        
        # Convert markdown to PDF
        import tempfile
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
        from reportlab.lib.enums import TA_LEFT, TA_CENTER
        import markdown2
        
        # Create temporary PDF file
        temp_file = tempfile.NamedTemporaryFile(
            suffix='.pdf', 
            delete=False
        )
        temp_file.close()
        
        # Convert markdown to HTML
        html_content = markdown2.markdown(markdown_content)
        
        # Create PDF
        doc = SimpleDocTemplate(temp_file.name, pagesize=letter,
                                rightMargin=72, leftMargin=72,
                                topMargin=72, bottomMargin=18)
        
        styles = getSampleStyleSheet()
        story = []
        
        # Split content into paragraphs and add to PDF
        for line in markdown_content.split('\n'):
            if line.strip():
                if line.startswith('# '):
                    # Main heading
                    p = Paragraph(line.replace('# ', ''), styles['Heading1'])
                elif line.startswith('## '):
                    # Section heading
                    p = Paragraph(line.replace('## ', ''), styles['Heading2'])
                elif line.startswith('### '):
                    # Subsection heading
                    p = Paragraph(line.replace('### ', ''), styles['Heading3'])
                elif line.startswith('**') and line.endswith('**'):
                    # Bold text
                    p = Paragraph(f'<b>{line.strip("*")}</b>', styles['Normal'])
                else:
                    # Normal text
                    p = Paragraph(line, styles['Normal'])
                story.append(p)
                story.append(Spacer(1, 0.1*inch))
        
        doc.build(story)
        
        try:
            # Upload to RAG endpoint (with retry logic for expired tokens)
            rag_url = os.getenv('RAG_API_URL', 'https://be-ext.algofolks.com/api/rag/upload')
            
            # Loop for retry (max 2 attempts)
            for attempt in range(1, 3):
                auth_token = os.getenv('RAG_AUTH_TOKEN', '')
                logger.info(f"RAG Upload attempt {attempt} (Token present: {bool(auth_token)})")
                
                # Create descriptive filename with position and company
                import re
                clean_position = re.sub(r'[^\w\s-]', '', position).strip().replace(' ', '_')
                clean_company = re.sub(r'[^\w\s-]', '', company).strip().replace(' ', '_')
                filename = f'{clean_position}_at_{clean_company}_Interview_Prep.pdf'
                
                with open(temp_file.name, 'rb') as f:
                    files = {'document': (filename, f, 'application/pdf')}
                    
                    metadata = {
                        'jobId': job_id,
                        'position': position,
                        'company': company,
                        'position_company': f'{position}_{company}',
                        'type': 'interview_prep',
                        'uploadedAt': data.get('scrapedAt', '')
                    }
                    
                    form_data = {'metadata': json.dumps(metadata)}
                    headers = {}
                    if auth_token:
                        headers['Authorization'] = f'Bearer {auth_token}'
                    
                    response = http_requests.post(rag_url, files=files, data=form_data, headers=headers, timeout=30)
                    
                    if response.status_code in [200, 201]:
                        logger.info(f"✅ Interview prep data uploaded to Vector DB (Attempt {attempt})")
                        questions_count = len(scraped_data.get('questions', {}).get('questions', []))
                        return jsonify({
                            "success": True,
                            "message": "Interview prep data uploaded to Vector DB successfully",
                            "jobId": job_id,
                            "questionsCount": questions_count,
                            "ragResponse": response.json()
                        }), 200
                    
                    elif response.status_code == 401 and attempt == 1:
                        logger.warning("RAG token expired (401). Refreshing...")
                        new_token = refresh_rag_token()
                        if not new_token:
                            logger.error("Failed to refresh RAG token. Cannot retry.")
                            break # Don't retry if refresh failed
                        logger.info("Token refreshed successfully. Retrying upload...")
                        # Continue to next attempt loop
                    
                    else:
                        logger.error(f"RAG upload failed (Attempt {attempt}): {response.status_code} - {response.text}")
                        if attempt == 2 or response.status_code != 401:
                            return jsonify({
                                "success": False,
                                "error": f"RAG upload failed: {response.status_code}",
                                "details": response.text
                            }), 500
        finally:
            # Clean up temp file
            os.unlink(temp_file.name)
            
    except Exception as e:
        logger.error(f"❌ Error saving interview prep data: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

def format_interview_prep_as_markdown(job_id, position, company, scraped_data):
    """
    Format scraped interview prep data as markdown for vector DB.
    """
    md = f"# Interview Preparation: {position} at {company}\n\n"
    md += f"**Job ID:** {job_id}\n"
    md += f"**Scraped At:** {scraped_data.get('scrapedAt', 'N/A')}\n\n"
    md += "---\n\n"
    
    # Questions section
    questions_data = scraped_data.get('questions', {})
    if questions_data:
        md += "## 📋 Interview Questions\n\n"
        
        # Fit Analysis
        fit_analysis = questions_data.get('fitAnalysis', '')
        if fit_analysis:
            md += f"### Fit Analysis\n\n{fit_analysis}\n\n"
        
        # Questions
        questions = questions_data.get('questions', [])
        if questions:
            md += f"### Questions ({len(questions)} total)\n\n"
            
            current_section = None
            for i, q in enumerate(questions, 1):
                section = q.get('section', 'General')
                if section != current_section:
                    md += f"\n#### {section}\n\n"
                    current_section = section
                
                question_text = q.get('question', 'N/A')
                answer_text = q.get('answer', 'N/A')
                tags = q.get('tags', [])
                difficulty = q.get('difficulty', 'unknown')
                
                md += f"**Q{i}:** {question_text}\n\n"
                md += f"**Answer:** {answer_text}\n\n"
                if tags:
                    md += f"*Tags: {', '.join(tags)}* | *Difficulty: {difficulty}*\n\n"
                md += "---\n\n"
    
    # Insights section
    insights_data = scraped_data.get('insights', {})
    if insights_data and insights_data.get('insights'):
        insights = insights_data['insights']
        md += "## 💡 Company Insights\n\n"
        
        # Culture
        if insights.get('culture'):
            md += f"### Culture\n\n{insights['culture']}\n\n"
        
        # Values
        if insights.get('values'):
            md += "### Values\n\n"
            for value in insights['values']:
                md += f"- {value}\n"
            md += "\n"
        
        # Interview Process
        if insights.get('interviewProcess'):
            md += f"### Interview Process\n\n{insights['interviewProcess']}\n\n"
        
        # Company-Specific Tips
        if insights.get('companySpecificTips'):
            md += "### Company-Specific Tips\n\n"
            for tip in insights['companySpecificTips']:
                md += f"- {tip}\n"
            md += "\n"
        
        # Salary Insights
        salary = insights.get('salaryInsights', {})
        if salary:
            md += "### 💰 Salary Insights\n\n"
            if salary.get('estimatedRange'):
                md += f"**Estimated Range:**\n\n{salary['estimatedRange']}\n\n"
            if salary.get('negotiationTips'):
                md += "**Negotiation Tips:**\n\n"
                for tip in salary['negotiationTips']:
                    md += f"- {tip}\n"
                md += "\n"
        
        # Red Flags
        if insights.get('redFlags'):
            md += "### 🚩 Red Flags to Avoid\n\n"
            for flag in insights['redFlags']:
                md += f"- {flag}\n"
            md += "\n"
    
    md += "\n---\n\n"
    md += f"*Document generated for vector search and RAG queries*\n"
    
    return md


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
        filename = pdf_service.generate_pdf(html_content, job_id, title, resume_edit_url, company)
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
            # Check if email was auto-sent or requires manual review
            auto_sent = draft_metadata.get('autoSent', False)
            placeholders_found = draft_metadata.get('placeholdersFound', 0)
            llm_ready = draft_metadata.get('llmReady', True)
            llm_reason = draft_metadata.get('llmReason')
            
            # Use defaultResume path if available, otherwise fallback to LBJ dynamic one
            actual_attachment_path = draft_metadata.get('attachmentPath', pdf_path)
            final_filename = os.path.basename(actual_attachment_path) if actual_attachment_path else filename

            if auto_sent:
                # Email was automatically sent
                telegram_lines = [
                    f"✅ <b>Email Sent Automatically</b>",
                    f"",
                    f"🏢 <b>Company:</b> {company}",
                    f"💼 <b>Role:</b> {title}",
                    f"📧 <b>To:</b> {job_data.get('applyEmail')}",
                    f"📎 <b>File:</b> {final_filename}",
                    f"🆔 <b>Message ID:</b> {draft_metadata.get('gmailDraftId', 'N/A')}",
                    f"",
                    f"✨ <b>No placeholders detected - email sent automatically!</b>",
                ]
            else:
                # Draft created for manual review (placeholders found)
                telegram_lines = [
                    f"📝 <b>Gmail Draft Created - Manual Review Required</b>",
                    f"",
                    f"🏢 <b>Company:</b> {company}",
                    f"💼 <b>Role:</b> {title}",
                    f"📧 <b>To:</b> {job_data.get('applyEmail')}",
                    f"📎 <b>File:</b> {final_filename}",
                    f"🆔 <b>Draft ID:</b> {draft_metadata.get('gmailDraftId', 'N/A')}",
                ]
                if placeholders_found > 0:
                    telegram_lines.append(f"")
                    telegram_lines.append(f"⚠️ <b>Found {placeholders_found} placeholder(s) - please review and fill them in</b>")
                
                if not llm_ready and llm_reason:
                    telegram_lines.append(f"")
                    telegram_lines.append(f"🤖 <b>LLM Review:</b> {llm_reason}")
            
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
            
            if not auto_sent:
                telegram_lines.append(f"\nReview and send from your Gmail Drafts.")

            telegram_msg = '\n'.join(telegram_lines)

            # Inline keyboard with Update Draft button (only for drafts, not auto-sent)
            reply_markup = None
            if not auto_sent:
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

            email_pipeline.send_telegram_notification(telegram_msg, actual_attachment_path, reply_markup=reply_markup)

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
        resume_edit_url = data.get('resume_edit_url')

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
            company = job_data.get('company', 'Company')

            if not old_draft_id:
                logger.warning(f"No existing draft_id for job {job_id}. Will create a fresh draft.")

            # 2. Generate or decode PDF
            if resume_html:
                # Generate PDF from HTML using Puppeteer (preferred) or WeasyPrint (fallback)
                pdf_filename = pdf_service.generate_pdf(resume_html, job_id, title, resume_edit_url, company)
                if not pdf_filename:
                    return jsonify({"success": False, "error": "Failed to generate PDF from HTML"}), 500
                pdf_path = os.path.join(PDF_OUTPUT_DIR, pdf_filename)
                logger.info(f"Generated updated PDF from HTML: {pdf_path}")
            else:
                # Decode pre-built PDF from base64
                pdf_bytes = b64.b64decode(pdf_base64)
                # Use title_company format for consistency
                sanitized_title = re.sub(r'[^a-zA-Z0-9]', '_', title)
                sanitized_company = re.sub(r'[^a-zA-Z0-9]', '_', company)
                pdf_filename = data.get('pdf_filename', f"{sanitized_title}_{sanitized_company}_updated.pdf")
                pdf_path = os.path.join(PDF_OUTPUT_DIR, pdf_filename)
                with open(pdf_path, 'wb') as f:
                    f.write(pdf_bytes)
                logger.info(f"Saved updated PDF ({len(pdf_bytes)} bytes) to {pdf_path}")

            # ─── DEFAULT RESUME RESOLUTION ───
            actual_attachment_path = email_pipeline.get_default_resume_path()
            if not actual_attachment_path:
                error_msg = "⚠️ Default resume file not found in backend directory. Email blocked. Please add defaultResume file to backend folder."
                logger.error(error_msg)
                
                # Notify Telegram about the block
                telegram_err = [
                    f"❌ <b>Draft Update Blocked</b>",
                    f"",
                    f"🏢 <b>Company:</b> {job_data.get('company', 'Company')}",
                    f"💼 <b>Role:</b> {job_data.get('title', 'Role')}",
                    f"📧 <b>To:</b> {job_data.get('applyEmail', 'N/A')}",
                    f"",
                    f"🚩 <b>Error:</b> {error_msg}"
                ]
                email_pipeline.send_telegram_notification('\n'.join(telegram_err))
                
                return jsonify({"success": False, "error": "RESUME_NOT_FOUND", "message": error_msg}), 400
            # ────────────────────────────────

            # 3. Create new draft with same email content + updated PDF
            gmail_service = GmailService()
            to_email = job_data.get('applyEmail')
            subject = job_data.get('emailSubject')
            body = job_data.get('emailBody')

            # 3.5. Hard Body Check
            if not body or not body.strip():
                _update_draft_lock.pop(job_id, None)
                return jsonify({"success": False, "error": "Email body is empty. Blocking update."}), 400

            # 3.6. LLM Quality Gate & Placeholder Check
            import llm_service
            llm_result = llm_service.validate_email_content(subject, body)
            llm_ready = llm_result.get("status") == "READY_TO_SEND"
            llm_reason = llm_result.get("reason")
            
            from email_pipeline import detect_placeholders
            placeholders = detect_placeholders(body)
            
            should_auto_send = not placeholders and llm_ready

            # Fetch existing draft to ensure we have the latest threadId if possible
            existing_draft = None
            if old_draft_id:
                existing_draft = gmail_service.get_draft(old_draft_id)
            
            # Use threadId from existing draft if found, otherwise from job_data
            active_thread_id = gmail_thread_id
            if existing_draft:
                active_thread_id = existing_draft.get('message', {}).get('threadId', active_thread_id)
                logger.info(f"Using threadId {active_thread_id} from fetched draft.")

            success = False
            new_draft = None

            # Attempt to create in existing thread
            if active_thread_id:
                success, new_draft = gmail_service.create_draft_in_thread(
                    to_email, subject, body, actual_attachment_path, active_thread_id
                )

            # Fallback to new draft creation if thread-based failed or no thread_id
            if not success:
                if active_thread_id:
                    logger.info("Thread lookup failed or thread was deleted. Falling back to creating a new thread.")
                success, result = gmail_service.create_draft(
                    to_email, subject, body, actual_attachment_path
                )
                new_draft = result if success else None

            if success and should_auto_send:
                # If it's ready to send, send it automatically!
                new_draft_id = new_draft.get('id')
                logger.info(f"Draft {new_draft_id} is READY and has no placeholders. Auto-sending...")
                send_success = gmail_service.send_draft(new_draft_id)
                if send_success:
                    auto_sent = True
                else:
                    logger.error(f"Failed to auto-send updated draft {new_draft_id}")
                    auto_sent = False
            else:
                auto_sent = False

            if not success:
                logger.error(f"Failed to create new draft for job {job_id}: {new_draft}")
                return jsonify({"success": False, "error": f"Failed to create new draft: {new_draft}"}), 500

            # 4. Delete old Gmail draft ONLY after new one is successfully created
            if old_draft_id:
                delete_ok = gmail_service.delete_draft(old_draft_id)
                if delete_ok:
                    logger.info(f"Deleted old draft {old_draft_id} after creating new draft.")
                else:
                    logger.warning(f"Could not delete old draft {old_draft_id}, proceeding anyway.")

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
            display_filename = os.path.basename(actual_attachment_path)

            if auto_sent:
                telegram_lines = [
                    f"✅ <b>Email Sent Automatically (Updated)</b>",
                    f"",
                    f"🏢 <b>Company:</b> {company}",
                    f"💼 <b>Role:</b> {title}",
                    f"📧 <b>To:</b> {to_email}",
                    f"📎 <b>Updated Resume:</b> {display_filename}",
                    f"🆔 <b>Message ID:</b> {new_draft_id}",
                    f"",
                    f"✨ <b>Passes LLM quality gate - sent automatically!</b>",
                ]
            else:
                telegram_lines = [
                    f"✅ <b>Draft Updated Successfully</b>",
                    f"",
                    f"🏢 <b>Company:</b> {company}",
                    f"💼 <b>Role:</b> {title}",
                    f"📧 <b>To:</b> {to_email}",
                    f"📎 <b>Updated Resume:</b> {display_filename}",
                    f"🆔 <b>New Draft ID:</b> {new_draft_id}",
                ]
                if placeholders:
                    telegram_lines.append(f"\n⚠️ <b>Found {len(placeholders)} placeholders</b>")
                if not llm_ready and llm_reason:
                    telegram_lines.append(f"\n🤖 <b>LLM Review:</b> {llm_reason}")

            if resume_edit_url:
                telegram_lines.append(f"\n✏️ <b>Edit Resume:</b>\n{resume_edit_url}")
            
            if auto_sent:
                telegram_lines.append(f"\nEmail was sent automatically after the update.")
            else:
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
                                          document_path=actual_attachment_path, reply_markup=update_markup)
            else:
                email_pipeline.send_telegram_notification(
                    telegram_msg, actual_attachment_path, reply_markup=update_markup)

            # 8. Clear pending action
            _clear_pending_action(job_id)

            return jsonify({
                "success": True,
                "newDraftId": new_draft_id,
                "updatedPdf": pdf_filename,
                "autoSent": auto_sent
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

    # 2. Check for duplicate using file-based store (VM-safe)
    actions = _load_pending_actions()
    existing = actions.get(job_id)
    if existing and existing.get('status') in ('pending', 'processing'):
        # Auto-clear if expired (>300s)
        if time.time() - existing.get('created_at', 0) > 300:
            del actions[job_id]
            _save_pending_actions(actions)
            _update_draft_lock.pop(job_id, None)
            logger.info(f"Cleared expired pending action for job {job_id}, allowing retry.")
        else:
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

    # Resolve 409 Conflict by deleting any existing webhook before polling
    try:
        logger.info("Auto-resolving Telegram conflict by deleting existing webhook...")
        http_requests.post(f"https://api.telegram.org/bot{bot_token}/deleteWebhook", timeout=10)
    except Exception as e:
        logger.error(f"Failed to delete Telegram webhook: {e}")

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
