# app/extraction/pdf_extractor.py
"""
PDF extraction using pdfplumber.
Wraps existing PDF extraction logic into a clean interface.
"""

import os
import tempfile
from typing import List, Optional

import requests
import pdfplumber


def extract_from_pdf_url(pdf_url: str, max_pages: Optional[int] = None) -> Optional[str]:
    """
    Download and extract text from a PDF URL.
    
    Args:
        pdf_url: URL of the PDF to download and parse
        max_pages: Maximum number of pages to parse (None = all pages)
    
    Returns:
        Extracted text from the PDF, or None if extraction fails
    """
    try:
        print(f"[INFO] Downloading PDF from {pdf_url}")
        
        # Download PDF with timeout and user agent
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(pdf_url, headers=headers, timeout=30, stream=True)
        response.raise_for_status()
        
        # Check content type
        content_type = response.headers.get('content-type', '').lower()
        if 'pdf' not in content_type and not pdf_url.lower().endswith('.pdf'):
            print(f"[WARN] URL may not be a PDF: {content_type}")
        
        # Save to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
            tmp_file.write(response.content)
            tmp_path = tmp_file.name
        
        try:
            # Parse PDF
            print(f"[INFO] Parsing PDF from {pdf_url}")
            text_parts = []
            
            with pdfplumber.open(tmp_path) as pdf:
                total_pages = len(pdf.pages)
                pages_to_parse = min(total_pages, max_pages) if max_pages else total_pages
                
                print(f"[INFO] PDF has {total_pages} pages, parsing {pages_to_parse} pages")
                
                for i, page in enumerate(pdf.pages[:pages_to_parse]):
                    try:
                        page_text = page.extract_text()
                        if page_text:
                            text_parts.append(page_text)
                    except Exception as e:
                        print(f"[WARN] Failed to extract text from page {i+1}: {e}")
                        continue
            
            extracted_text = "\n\n".join(text_parts)
            print(f"[INFO] Extracted {len(extracted_text)} characters from PDF")
            
            return extracted_text if extracted_text else None
            
        finally:
            # Clean up temporary file
            try:
                os.unlink(tmp_path)
            except Exception as e:
                print(f"[WARN] Failed to delete temp file {tmp_path}: {e}")
                
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to download PDF from {pdf_url}: {e}")
        return None
    except Exception as e:
        print(f"[ERROR] Failed to parse PDF from {pdf_url}: {e}")
        import traceback
        traceback.print_exc()
        return None


def extract_from_pdf_urls(pdf_urls: List[str], max_pages_per_pdf: Optional[int] = None) -> Optional[str]:
    """
    Extract text from multiple PDF URLs and combine their text.
    
    Args:
        pdf_urls: List of PDF URLs to parse
        max_pages_per_pdf: Maximum number of pages to parse per PDF
    
    Returns:
        Combined text from all PDFs, or None if no text extracted
    """
    if not pdf_urls:
        return None
    
    all_texts = []
    for i, pdf_url in enumerate(pdf_urls, 1):
        print(f"[INFO] Parsing PDF {i}/{len(pdf_urls)}: {pdf_url}")
        pdf_text = extract_from_pdf_url(pdf_url, max_pages=max_pages_per_pdf)
        if pdf_text:
            # Add separator between PDFs
            if all_texts:
                all_texts.append(f"\n\n--- PDF {i} ({pdf_url}) ---\n\n")
            all_texts.append(pdf_text)
        else:
            print(f"[WARN] No text extracted from PDF {i}: {pdf_url}")
    
    if not all_texts:
        return None
    
    return "".join(all_texts)
