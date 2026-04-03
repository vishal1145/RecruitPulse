import os
import json
import logging
import anthropic
import config

# Configure logging
logger = logging.getLogger(__name__)

def validate_email_content(subject, body):
    """
    Validates the email content using Claude Sonnet.
    Falls back to Groq Llama3 if Anthropic fails.
    """
    try:
        # 1. Try Anthropic First
        return _validate_with_anthropic(subject, body)
    except Exception as e:
        logger.warning(f"⚠️ Anthropic API call failed: {e}. Switching to Groq fallback...")
        
        # 2. Automatically Switch to Groq
        return _validate_with_groq(subject, body)


def _validate_with_anthropic(subject, body):
    """
    Primary validation logic using Anthropic Claude Sonnet (Hardcoded).
    """
    # Hardcoded for immediate stability
    api_key = 
    model = "claude-3-5-sonnet-20240620"

    client = anthropic.Anthropic(api_key=api_key)
    system_prompt = get_system_prompt()
    user_content = f"Subject: {subject}\n\nBody:\n{body}"
    
    response = client.messages.create(
        model=model,
        max_tokens=256,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
        temperature=0
    )
    
    content_text = response.content[0].text.strip()
    return parse_llm_json(content_text)


def _validate_with_groq(subject, body):
    """
    Fallback validation logic using Groq API (Hardcoded).
    """
    # Hardcoded for immediate stability
    api_key = 
    model = "llama-3.1-8b-instant"

    try:
        import requests
        logger.info(f"Calling Groq API fallback...")
        
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": get_system_prompt()},
                {"role": "user", "content": f"Subject: {subject}\n\nBody:\n{body}"}
            ],
            "temperature": 0
        }
        
        # We use headers=headers to ensure Bearer token is sent correctly
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        
        data = response.json()
        content_text = data["choices"][0]["message"]["content"].strip()
        
        result = parse_llm_json(content_text)
        
        # Add reminder/log as requested
        logger.info("⚠️ Anthropic API limit reached or failed. Currently using Groq as fallback.")
        
        return result
        
    except Exception as e:
        logger.error(f"Error calling Groq API: {e}")
        # Final guardrail for both failing
        return {
            "status": "NEEDS_REVIEW", 
            "reason": "⚠️ Both Anthropic and Groq API calls failed. Email moved to draft for manual review."
        }


def get_system_prompt():
    return """You are an email quality checker for job applications. 
Analyze the email content provided and determine if it is complete and ready to send.

Flag the email as NEEDS_REVIEW if any of the following are true:
- There are unfilled placeholders like [Company Name], {{position}}, <insert here>, or similar
- The email body is missing or empty
- The content seems generic, incomplete, or cut off mid-sentence
- The job role or company name appears incorrect or mismatched
- The tone is unprofessional or the email seems broken

If none of the above issues are found, mark it as READY_TO_SEND.

Respond only in this JSON format:
{
  "status": "READY_TO_SEND" or "NEEDS_REVIEW",
  "reason": "brief explanation if NEEDS_REVIEW, else null"
}"""


def parse_llm_json(content_text):
    try:
        result = json.loads(content_text)
        status = result.get("status", "NEEDS_REVIEW")
        reason = result.get("reason")
        logger.info(f"LLM Validation Result: {status}")
        return {"status": status, "reason": reason}
    except json.JSONDecodeError:
        logger.error(f"Failed to parse LLM response as JSON: {content_text}")
        return {
            "status": "NEEDS_REVIEW", 
            "reason": "LLM validation produced invalid JSON."
        }
