from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import json
from datetime import datetime
import fcntl
import logging
from job_email_service import JobEmailService
from pdf_service import PdfService
import email_pipeline

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Enable CORS for all origins so the extension can POST data
CORS(app)

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE_PATH = os.path.join(BASE_DIR, 'jobs.json')
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

def update_job_sent_status(job_id):
    """Updates jobs.json to mark a job as emailed."""
    try:
        jobs = load_jobs_from_json()
        updated = False
        for job in jobs:
            if job.get('jobId') == job_id:
                job['emailSent'] = True
                job['emailSentAt'] = datetime.utcnow().isoformat()
                updated = True
                break

        if updated:
            save_jobs_to_json(jobs)
        return updated
    except Exception as e:
        logger.error(f"Error updating jobs.json for {job_id}: {e}")
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

        # 3. Send Email with Attachment
        email_sent = email_pipeline.send_email_with_attachment(job_data, pdf_path)
        
        if email_sent:
            # 4. Update jobs.json
            update_job_sent_status(job_id)
            
            # 5. Send Telegram Notification
            telegram_msg = (
                f"✅ <b>Email Sent with Attachment</b>\n"
                f"Job: {title}\n"
                f"Company: {company}\n"
                f"To: {job_data.get('applyEmail')}\n"
                f"File: {filename}"
            )
            email_pipeline.send_telegram_notification(telegram_msg)

            return jsonify({
                "success": True, 
                "emailSent": True,
                "filename": filename,
                "downloadUrl": f"http://localhost:5350/downloads/{filename}"
            }), 200
        else:
            return jsonify({
                "success": True, 
                "emailSent": False, 
                "error": "Email failed to send but PDF was generated",
                "filename": filename
            }), 200 # Return 200 but notify about email failure

    except Exception as e:
        logger.error(f"❌ Error in /api/generate-resume-pdf: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

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
    print(f"🚀 Server running on http://localhost:{port}")
    print(f"📂 Data will be saved to: {JSON_FILE_PATH}")
    app.run(host='0.0.0.0', port=port, debug=True)
