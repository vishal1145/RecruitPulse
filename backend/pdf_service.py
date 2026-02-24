import os
import logging
from datetime import datetime
from weasyprint import HTML
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PdfService:
    def __init__(self, output_dir="generated_pdfs"):
        self.output_dir = output_dir
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
            logger.info(f"Created PDF output directory: {self.output_dir}")

    def sanitize_filename(self, filename):
        """Removes illegal characters from filename."""
        return re.sub(r'[^a-zA-Z0-9]', '_', filename)

    def generate_pdf(self, html_content, jobId, title):
        """Generates a PDF from HTML content using WeasyPrint."""
        try:
            sanitized_job_id = self.sanitize_filename(jobId)
            filename = f"RecruitPulse_{sanitized_job_id}.pdf"
            filepath = os.path.join(self.output_dir, filename)

            # Generate PDF
            logger.info(f"Generating PDF for Job ID: {jobId} ({title})...")
            HTML(string=html_content).write_pdf(filepath)
            
            logger.info(f"Successfully generated PDF: {filepath}")
            return filename
        except Exception as e:
            logger.error(f"Failed to generate PDF: {e}")
            return None
