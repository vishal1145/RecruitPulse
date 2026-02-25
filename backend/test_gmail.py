import os
import sys
import logging
from gmail_service import GmailService

# Configure logging to see what's happening
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_draft_creation():
    """
    Independent script to test Gmail authentication and draft creation.
    """
    print("--- Gmail Draft Integration Test ---")
    
    # 1. Setup sample data
    to_email = "test@example.com" # You can change this to your own email for testing
    subject = "Test Gmail Draft from RecruitPulse"
    body = "Hello!\n\nThis is a test draft created via the Gmail API integration. If you see this, the authentication works!"
    
    # We'll try to find an existing PDF in generated_pdfs if available, otherwise skip attachment
    attachment_path = None
    pdf_dir = os.path.join(os.path.dirname(__file__), 'generated_pdfs')
    if os.path.exists(pdf_dir):
        files = [f for f in os.listdir(pdf_dir) if f.endswith('.pdf')]
        if files:
            attachment_path = os.path.join(pdf_dir, files[0])
            print(f"Using sample attachment: {files[0]}")

    # 2. Initialize Gmail Service and create draft
    try:
        print("Initializing Gmail Service (this may open a browser window for first-time login)...")
        service = GmailService()
        
        print(f"Creating draft for {to_email}...")
        success, result = service.create_draft(to_email, subject, body, attachment_path)
        
        if success:
            print("\n✅ SUCCESS: Draft created!")
            print(f"Draft ID: {result['id']}")
            print("\nNext steps:")
            print("1. Go to your Gmail 'Drafts' folder.")
            print("2. Look for the message with the subject: '" + subject + "'")
            if attachment_path:
                print("3. Verify the PDF is attached.")
        else:
            print(f"\n❌ FAILURE: {result}")
            
    except Exception as e:
        print(f"\n❌ ERROR during test: {e}")

if __name__ == "__main__":
    # Ensure we are in the backend directory context for imports
    test_draft_creation()
