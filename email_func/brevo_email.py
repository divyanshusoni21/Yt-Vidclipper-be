import base64
import requests
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from utility.variables import projectName, adminEmail, frontendDomain,brevoApiKey,projectLogo
from yt_helper.settings import logger
import traceback
from datetime import datetime

class BrevoEmail:
    """
    Brevo (formerly Sendinblue) email class that maintains the same interface as the original Email class.
    
    Setup:
    1. Install requests: pip install requests
    2. Set your Brevo API key in the class variable or environment variable
    """
    
    # Brevo API configuration
    BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
    
    # You can set this as an environment variable or replace with your actual API key
    # import os
    # API_KEY = os.getenv('BREVO_API_KEY', 'your-brevo-api-key')
    API_KEY = brevoApiKey
    
    @classmethod
    def _get_headers(cls):
        """Get the required headers for Brevo API"""
        return {
            'accept': 'application/json',
            'api-key': cls.API_KEY,
            'content-type': 'application/json'
        }
    
    @classmethod
    def _prepare_recipients(cls, to_email, cc_emails=None):
        """Prepare the recipients list for Brevo API"""
        if not isinstance(to_email, list):
            to_email = [to_email]
            
        recipients = [{"email": email} for email in to_email]
        
        if cc_emails:
            if not isinstance(cc_emails, list):
                cc_emails = [cc_emails]
            recipients.extend([{"email": email} for email in cc_emails])
            
        return recipients
    
    @classmethod
    def _prepare_attachments(cls, data):
        """Prepare attachments for Brevo API"""
        attachments = []

        # Handle CSV files from paths
        csv_files_paths = data.get("csv_files_paths") or []
        if csv_files_paths:
            for file_path in csv_files_paths:
                try:
                    with open(file_path, 'rb') as f:
                        file_content = f.read()
                        attachments.append({
                            "name": file_path.split('/')[-1],
                            "content": base64.b64encode(file_content).decode('utf-8')
                        })
                except Exception as e:
                    logger.warning(f"Failed to attach CSV file {file_path}: {e}")

        # Handle file from URL
        file_attach_url = data.get("file_attach_url")
        if file_attach_url:
            try:
                response = requests.get(file_attach_url["url"])
                if response.status_code == 200:
                    attachments.append({
                        "name": file_attach_url["file_name"],
                        "content": base64.b64encode(response.content).decode('utf-8')
                    })
            except Exception as e:
                logger.warning(f"Failed to attach file from URL: {e}")

        # Handle direct file content
        attach_file = data.get("attach_file")
        if attach_file:
            try:
                if isinstance(attach_file["file_content"], str):
                    file_content = attach_file["file_content"].encode('utf-8')
                else:
                    file_content = attach_file["file_content"]

                attachments.append({
                    "name": attach_file["file_name"],
                    "content": base64.b64encode(file_content).decode('utf-8')
                })
            except Exception as e:
                logger.warning(f"Failed to attach direct file content: {e}")

        return attachments
    
    @classmethod
    def send_email(cls, data):
        """
        Send email using Brevo API
        
        Args:
            data (dict): Email data containing:
                - to_email: recipient email (str or list)
                - email_subject: email subject (str)
                - email_body: email body (dict with 'type' for template or str)
                - cc_mail: CC recipients (str or list, optional)
                - csv_files_paths: list of CSV file paths (optional)
                - file_attach_url: dict with url, file_type, file_name (optional)
                - attach_file: dict with file_name, file_content, file_content_type (optional)
        
        Returns:
            dict: Response from Brevo API
        """
        try:
            current_year = datetime.now().year
            
            # Prepare email content
            if isinstance(data["email_body"], dict) and "type" in data["email_body"]:
                # Render template
                template_name = data["email_body"].pop("type") + ".html"
                context = {
                    **data["email_body"],
                    "projectName": projectName,
                    "adminEmail": adminEmail,
                    "frontendDomain": frontendDomain,
                    "currentYear": current_year,
                    "projectLogo": projectLogo,

                    
                }
      
                html_content = render_to_string(template_name, context)
                text_content = strip_tags(html_content)
            else:
                # Use plain text
                text_content = str(data["email_body"])
                html_content = f"<html><body><p>{text_content}</p></body></html>"
            
            # Prepare the payload for Brevo API
            payload = {
                "sender": {
                    "name": projectName,
                    "email": adminEmail
                },
                "to": [{"email": email} for email in (
                    [data["to_email"]] if isinstance(data["to_email"], str) 
                    else data["to_email"]
                )],
                "subject": data["email_subject"],
                "htmlContent": html_content,
                "textContent": text_content,
            }
            
            # Add CC if provided
            if data.get("cc_mail"):
                payload["cc"] = [{"email": email} for email in (
                    [data["cc_mail"]] if isinstance(data["cc_mail"], str) 
                    else data["cc_mail"]
                )]
            
            # Add attachments if any
            attachments = cls._prepare_attachments(data)
            if attachments:
                payload["attachment"] = attachments
            
            # Send the email with a 30s timeout so network drops fail fast instead of hanging
            response = requests.post(
                cls.BREVO_API_URL,
                json=payload,
                headers=cls._get_headers(),
                timeout=30,
            )

            # Log the result
            if response.status_code == 201:
                logger.info(f"Brevo email sent to {data['to_email']}, subject: {data['email_subject']}")
            elif response.status_code in (429, 500, 502, 503, 504):
                # Rate limit or remote server error - raise so Celery task can retry with backoff
                logger.error(f"Brevo server error ({response.status_code}): {response.text}")
                response.raise_for_status()
            else:
                logger.error(f"Failed to send Brevo email: {response.status_code} - {response.text}")

            return {
                "status_code": response.status_code,
                "message": response.text,
                "success": response.status_code == 201,
            }

        except requests.RequestException as e:
            # Re-raise network drops, timeouts, and 5xx/429 HTTP errors so Celery can autoretry
            logger.error(f"Network or API error sending Brevo email: {e}")
            raise
        except Exception as e:
            errorMsg = f"Error sending Brevo email: {e}"
            logger.error(errorMsg)
            logger.error(traceback.format_exc())
            return {
                "status_code": 500,
                "message": errorMsg,
                "success": False,
            }

# Alias for backward compatibility
Email = BrevoEmail
