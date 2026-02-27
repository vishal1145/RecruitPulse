import os
import logging
from gmail_auth import get_gmail_credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


def _get_docs_service():
    """Uses the user's OAuth credentials (same as Gmail) to access Docs API."""
    creds = get_gmail_credentials()
    return build('docs', 'v1', credentials=creds)


def _get_drive_service():
    """Uses the user's OAuth credentials (same as Gmail) to access Drive API."""
    creds = get_gmail_credentials()
    return build('drive', 'v3', credentials=creds)


def create_resume_doc(job_id, resume_text, title_prefix="Resume"):
    """
    Creates a Google Doc with the given resume text.

    Args:
        job_id: The job identifier (used in the doc title).
        resume_text: Plain text content of the resume.
        title_prefix: Optional prefix for the document title.

    Returns:
        doc_id (str) on success, None on failure.
    """
    try:
        docs_service = _get_docs_service()

        # Create a blank document
        doc_title = f"{title_prefix} - {job_id}"
        doc = docs_service.documents().create(body={"title": doc_title}).execute()
        doc_id = doc.get("documentId")
        logger.info(f"Created Google Doc: {doc_id} ('{doc_title}')")

        # Insert the resume text into the document
        if resume_text:
            requests_body = [
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": resume_text,
                    }
                }
            ]
            docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": requests_body}
            ).execute()
            logger.info(f"Inserted resume text into doc {doc_id}")

        return doc_id

    except Exception as e:
        logger.error(f"Failed to create Google Doc for job {job_id}: {e}")
        return None


def share_doc_with_anyone(doc_id):
    """
    Shares the document so 'anyone with the link' can edit.
    No email required — the user just opens the link.

    Returns True on success, False on failure.
    """
    try:
        drive_service = _get_drive_service()

        permission = {
            "type": "anyone",
            "role": "writer",
        }
        drive_service.permissions().create(
            fileId=doc_id,
            body=permission,
            fields="id",
        ).execute()

        logger.info(f"Shared doc {doc_id} with 'anyone with link can edit'")
        return True

    except Exception as e:
        logger.error(f"Failed to share doc {doc_id}: {e}")
        return False


def export_doc_as_pdf(doc_id, output_path):
    """
    Exports a Google Doc as a PDF file.

    Args:
        doc_id: The Google Doc ID.
        output_path: Local file path to save the PDF.

    Returns:
        True on success, False on failure.
    """
    try:
        drive_service = _get_drive_service()

        # Export the document as PDF
        response = drive_service.files().export(
            fileId=doc_id,
            mimeType='application/pdf'
        ).execute()

        # Write to file
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'wb') as f:
            f.write(response)

        logger.info(f"Exported doc {doc_id} as PDF to {output_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to export doc {doc_id} as PDF: {e}")
        return False


def get_edit_url(doc_id):
    """Returns the editable Google Docs URL."""
    return f"https://docs.google.com/document/d/{doc_id}/edit"
