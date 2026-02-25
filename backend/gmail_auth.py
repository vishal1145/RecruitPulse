import os
import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import logging
import config

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/gmail.compose']

logger = logging.getLogger(__name__)

def get_gmail_credentials():
    """
    Handles OAuth2 authentication and returns credentials.
    """
    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    token_path = os.path.join(os.path.dirname(__file__), 'token.json')
    
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing Gmail token...")
            creds.refresh(Request())
        else:
            logger.info("Initializing new Gmail OAuth flow...")
            
            # Construct client config from environment variables
            client_config = {
                "installed": {
                    "client_id": config.GMAIL_CLIENT_ID,
                    "project_id": config.GMAIL_PROJECT_ID,
                    "auth_uri": config.GMAIL_AUTH_URI,
                    "token_uri": config.GMAIL_TOKEN_URI,
                    "auth_provider_x509_cert_url": config.GMAIL_AUTH_PROVIDER_X509_CERT_URL,
                    "client_secret": config.GMAIL_CLIENT_SECRET,
                    "redirect_uris": config.GMAIL_REDIRECT_URIS
                }
            }
            
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            # Use open_browser=False for headless server environments
            creds = flow.run_local_server(
                port=0, 
                open_browser=False, 
                authorization_prompt_message='Please visit this URL to authorize RecruitPulse: {url}'
            )
            
        # Save the credentials for the next run
        with open(token_path, 'w') as token:
            token.write(creds.to_json())

    return creds

def get_gmail_service():
    """
    Returns a Gmail API service instance.
    """
    creds = get_gmail_credentials()
    service = build('gmail', 'v1', credentials=creds)
    return service
