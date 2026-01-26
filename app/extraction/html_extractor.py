# app/extraction/html_extractor.py
"""
HTML extraction using trafilatura for clean main content extraction.
Removes navigation, footers, ads, and other boilerplate.
"""

import json
from typing import Optional


def extract_from_html(html: str, url: Optional[str] = None) -> Optional[dict]:
    """
    Extract clean main text and metadata from HTML using trafilatura.
    
    Args:
        html: Raw HTML string to extract from
        url: Optional URL for better extraction context
    
    Returns:
        Dict with title, author, date, language, text or None if extraction fails
    """
    try:
        import trafilatura
        
        extracted = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            include_links=False,
            output_format="json"
        )
        
        if not extracted:
            print(f"[WARN] trafilatura returned no content for URL: {url}")
            return None
        
        data = json.loads(extracted)
        
        text = (data.get("text") or "").strip()
        if not text:
            print(f"[WARN] trafilatura extracted empty text for URL: {url}")
            return None
        
        return {
            "title": data.get("title"),
            "author": data.get("author"),
            "date": data.get("date"),
            "language": data.get("language"),
            "text": text,
        }
        
    except Exception as e:
        print(f"[ERROR] trafilatura extraction failed: {e}")
        return None


def extract_from_url(url: str, timeout: int = 30) -> Optional[dict]:
    """
    Fetch a URL and extract clean main content using trafilatura.
    
    Args:
        url: URL to fetch and extract from
        timeout: Request timeout in seconds
    
    Returns:
        Dict with title, author, date, language, text or None if extraction fails
    """
    try:
        import trafilatura
        
        print(f"[INFO] Fetching URL for extraction: {url}")
        
        # trafilatura has built-in fetching that handles many edge cases
        downloaded = trafilatura.fetch_url(url)
        
        if not downloaded:
            print(f"[WARN] trafilatura could not fetch URL: {url}")
            return None
        
        return extract_from_html(downloaded, url=url)
        
    except Exception as e:
        print(f"[ERROR] Failed to fetch/extract URL {url}: {e}")
        return None
