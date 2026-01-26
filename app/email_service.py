# app/email_service.py
"""Email service for sending verification codes."""

import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from .config import PREFERENCES

logger = logging.getLogger(__name__)


def send_verification_email(to_email: str, code: str) -> bool:
    """
    Send a verification code email.
    
    In dev mode (EMAIL_DEV_MODE=true), just logs the code to console.
    In production, sends via SMTP.
    
    Returns True if email was sent/logged successfully, False otherwise.
    """
    config = PREFERENCES.email
    
    # Dev mode: just log the code
    if config.dev_mode:
        logger.info(f"[DEV MODE] Verification code for {to_email}: {code}")
        print(f"\n{'='*50}")
        print(f"VERIFICATION CODE for {to_email}")
        print(f"Code: {code}")
        print(f"{'='*50}\n")
        return True
    
    # Production mode: send via SMTP
    if not config.smtp_host or not config.smtp_user:
        logger.error("SMTP not configured. Set SMTP_HOST and SMTP_USER environment variables.")
        return False
    
    try:
        # Create message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"VaultBubble Extension - Verification Code: {code}"
        msg["From"] = f"{config.from_name} <{config.from_email}>"
        msg["To"] = to_email
        
        # Plain text version
        text_content = f"""
Your VaultBubble Extension Verification Code

Code: {code}

This code will expire in 10 minutes.

If you didn't request this code, you can safely ignore this email.

- The VaultBubble Team
"""
        
        # HTML version
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
        .container {{ max-width: 480px; margin: 0 auto; padding: 20px; }}
        .code {{ font-size: 32px; font-weight: bold; letter-spacing: 4px; 
                 background: #f0f0f0; padding: 16px 24px; border-radius: 8px;
                 text-align: center; margin: 24px 0; }}
        .footer {{ color: #666; font-size: 12px; margin-top: 32px; }}
    </style>
</head>
<body>
    <div class="container">
        <h2>VaultBubble Extension</h2>
        <p>Use this code to connect your browser extension:</p>
        <div class="code">{code}</div>
        <p>This code will expire in 10 minutes.</p>
        <p class="footer">
            If you didn't request this code, you can safely ignore this email.
        </p>
    </div>
</body>
</html>
"""
        
        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))
        
        # Send email
        context = ssl.create_default_context()
        
        with smtplib.SMTP(config.smtp_host, config.smtp_port) as server:
            server.starttls(context=context)
            server.login(config.smtp_user, config.smtp_pass)
            server.sendmail(config.from_email, to_email, msg.as_string())
        
        logger.info(f"Verification email sent to {to_email}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send verification email to {to_email}: {e}")
        return False
