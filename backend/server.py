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
import config

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

        # 3. Create Gmail Draft with Attachment
        draft_created = email_pipeline.send_email_with_attachment(job_data, pdf_path)
        
        if draft_created:
            # 4. Update jobs.json
            update_job_sent_status(job_id)
            
            # 5. Send Telegram Notification
            telegram_msg = (
                f"📝 <b>Gmail Draft Created</b>\n"
                f"Job: {title}\n"
                f"Company: {company}\n"
                f"To: {job_data.get('applyEmail')}\n"
                f"File: {filename}\n"
                f"Review and send from your Gmail Drafts."
            )
            email_pipeline.send_telegram_notification(telegram_msg, pdf_path)

            return jsonify({
                "success": True, 
                "draftCreated": True,
                "filename": filename,
                "downloadUrl": f"{config.BASE_URL}/downloads/{filename}"
            }), 200
        else:
            return jsonify({
                "success": True, 
                "draftCreated": False, 
                "error": "Failed to create Gmail draft but PDF was generated",
                "filename": filename
            }), 200 # Return 200 but notify about draft failure

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
                "applyEmail": "vishal.gupta@algofolks.com",
                "company": "Adobe",
                "emailBody": "Dear Smit Shah,\n\nMy name is Gautham Madhu, and I am a Product Manager with 5-10 years of experience, currently at Axis Bank. I'm writing to express my strong interest in the Senior Product Manager for Growth position at Adobe. Having followed Adobe's innovative work, I am particularly drawn to this role's potential to significantly impact user acquisition and engagement.\n\nMy career has been centered around leading product initiatives and driving measurable outcomes. As a Product Manager at Axis Bank, I've honed my skills in strategic planning, market analysis, and product lifecycle management. Prior to this, my experience as a Team Lead at J. Edgerton Consulting (04/2015 - 04/2022) and Resolutions Team Lead at Mount Rose Technologies (10/2010 - 03/2015) further developed my leadership and team management capabilities, both critical for a growth-focused role.\n\nI possess a robust skill set that aligns well with the requirements for a Senior Product Manager for Growth, including strong leadership and teamwork, effective time management, customer service, and communication skills. My knowledge of digital performance metrics and risk management has consistently enabled me to make data-driven decisions that propel product success. I am also proficient in multiple languages (English, Spanish, Polish, French), which could be a valuable asset in a global organization like Adobe.\n\nI am confident that my experience and passion for driving product growth would allow me to make significant contributions to Adobe. I have attached my resume for your review and would be grateful for the opportunity to discuss how my skills and experience can benefit your team. Please let me know if a brief call would be possible at your convenience.\n\nThank you for your time and consideration.\n\nSincerely,\nGautham Madhu\ngauthammadhu27@gmail.com",
                "emailSent": False,
                "emailSubject": "Experienced Product Manager | Driving Growth at Adobe | Gautham Madhu",
                "experience": "",
                "fullDescription": "We're hiring a Senior PM for Growth at Adobe.\n\nIf you've ever wanted to work on tools that millions of creators use every day, here's your chance- We are looking for a Senior Product Manager to join our team building Premiere Pro and next-gen video tools.\n\nWhy this team?\nWe're reimagining how creators work with video, from AI-powered features like Object Masking and Generative Extend to completely new workflows that didn't exist a year ago.\nThe problems are hard. The impact is real. And genuinely one of the best team I've worked with. You'll collaborate with world-class researchers, designers, and engineers who care deeply about craft. You'll ship features that professional editors, filmmakers, and content creators rely on and help shape the future of video creation.\n\nWhat we're looking for-\nSomeone who understands growth, loves working cross-functionally, and gets excited about turning powerful tech to products people actually want to use.\nIf that sounds like you, let's talk. DM me\n\nhttps://lnkd.in/gfh58DUX",
                "hiringManager": "Overview\nOutreach\nEmail",
                "jdResumeBuilt": False,
                "jdResumeBuiltAt": "2026-02-24T06:31:53.609Z",
                "jobId": "job_483800157",
                "location": "",
                "processedAt": "2026-02-23T12:59:01.270Z",
                "shortDescription": "Senior Product Manager for Growth\n\nAdobe\n\nReviewed\nOverview\nOutreach\nEmail\nJob Post\n\nWe're hiring a Senior PM for Growth at Adobe.If you've ever wanted to work on tools that millions of creators use every day, here's your chance- We are looking for a Senior Product Manager to join our team building Premiere Pro and next-gen video tools.Why this team?We're reimagining how creators wo...\n\nView Full Post\nHiring Manager\nSS\nSmit Shah\u2019s\n\nPrincipal Product Manager- GenAI @ Adobe | Driving Product Lifec",
                "source": "linkedin",
                "title": "Senior Product Manager for Growth",
                "updatedAt": "2026-02-24T06:31:53.616565",
                "viewFullPostUrl": "https://www.linkedin.com/feed/update/urn:li:activity:7431195300490457088",
                "emailSentAt": "2026-02-24T06:31:52.726120"
            }
        ]
        save_jobs_to_json(test_jobs)
        return jsonify({"success": True, "message": "Jobs reset for testing"}), 200
    except Exception as e:
        logger.error(f"❌ Error resetting jobs: {e}")
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
    print(f"🚀 Server running on {config.BASE_URL}")
    print(f"📂 Data will be saved to: {JSON_FILE_PATH}")
    app.run(host='0.0.0.0', port=port, debug=True)
