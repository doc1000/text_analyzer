#!/usr/bin/env python3
"""Generate a reviewer verification code for extension testing.

This script creates long-lived, multi-use verification codes for:
- Chrome Web Store reviewers
- Beta testers (until email delivery is set up)

Usage:
    python generate_reviewer_code.py <email> [days]

Examples:
    python generate_reviewer_code.py reviewer@chromium.org
    python generate_reviewer_code.py tester@example.com 30
"""

import os
import random
import sys
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def generate_reviewer_code(email: str, days: int = 14) -> str | None:
    """Generate a 2-week (default) reviewer code for the given email.
    
    Args:
        email: Email address to associate with the code
        days: Number of days until expiration (default: 14)
    
    Returns:
        The generated 6-digit code, or None on error
    """
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print("ERROR: DATABASE_URL environment variable not set")
        print("Set it to your PostgreSQL connection string, e.g.:")
        print("  export DATABASE_URL='postgresql://user:pass@localhost/dbname'")
        return None
    
    try:
        engine = create_engine(database_url)
        Session = sessionmaker(bind=engine)
        db = Session()
        
        # Generate 6-digit code
        code = "".join([str(random.randint(0, 9)) for _ in range(6)])
        expires_at = datetime.utcnow() + timedelta(days=days)
        
        # Insert the reviewer code
        db.execute(text("""
            INSERT INTO extension_verification_codes 
            (id, email, code, purpose, created_at, expires_at)
            VALUES (gen_random_uuid(), :email, :code, 'reviewer', now(), :expires_at)
        """), {"email": email.strip().lower(), "code": code, "expires_at": expires_at})
        db.commit()
        db.close()
        
        print(f"\n{'='*60}")
        print(f"  REVIEWER CODE GENERATED")
        print(f"{'='*60}")
        print(f"  Email:    {email}")
        print(f"  Code:     {code}")
        print(f"  Expires:  {expires_at.strftime('%Y-%m-%d %H:%M UTC')} ({days} days)")
        print(f"  Purpose:  reviewer (multi-use)")
        print(f"{'='*60}")
        print(f"\n  For Chrome Web Store submission, include:")
        print(f"  ----------------------------------------")
        print(f"  Test Account Email: {email}")
        print(f"  Verification Code:  {code}")
        print(f"{'='*60}\n")
        
        return code
        
    except Exception as e:
        print(f"ERROR: Failed to generate code: {e}")
        return None


def list_reviewer_codes() -> None:
    """List all active reviewer codes."""
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print("ERROR: DATABASE_URL not set")
        return
    
    try:
        engine = create_engine(database_url)
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT email, code, created_at, expires_at, used_at
                FROM extension_verification_codes
                WHERE purpose = 'reviewer'
                AND expires_at > now()
                ORDER BY created_at DESC
            """))
            rows = result.fetchall()
            
            if not rows:
                print("No active reviewer codes found.")
                return
            
            print(f"\n{'='*80}")
            print(f"  ACTIVE REVIEWER CODES")
            print(f"{'='*80}")
            print(f"  {'Email':<30} {'Code':<8} {'Expires':<20} {'Last Used':<20}")
            print(f"  {'-'*30} {'-'*8} {'-'*20} {'-'*20}")
            for row in rows:
                email, code, created, expires, used = row
                expires_str = expires.strftime('%Y-%m-%d %H:%M') if expires else 'N/A'
                used_str = used.strftime('%Y-%m-%d %H:%M') if used else 'Never'
                print(f"  {email:<30} {code:<8} {expires_str:<20} {used_str:<20}")
            print(f"{'='*80}\n")
            
    except Exception as e:
        print(f"ERROR: {e}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nCommands:")
        print("  python generate_reviewer_code.py <email> [days]  - Generate a new code")
        print("  python generate_reviewer_code.py --list          - List active codes")
        sys.exit(1)
    
    if sys.argv[1] == "--list":
        list_reviewer_codes()
    else:
        email = sys.argv[1]
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 14
        
        if "@" not in email:
            print(f"ERROR: Invalid email address: {email}")
            sys.exit(1)
        
        generate_reviewer_code(email, days)


if __name__ == "__main__":
    main()
