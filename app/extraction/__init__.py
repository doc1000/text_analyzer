# app/extraction/__init__.py
"""
Extraction layer for converting raw documents into clean text.
Supports HTML (via trafilatura) and PDF (via pdfplumber).
"""

from .html_extractor import extract_from_url, extract_from_html
from .pdf_extractor import extract_from_pdf_url, extract_from_pdf_urls

__all__ = [
    "extract_from_url",
    "extract_from_html",
    "extract_from_pdf_url",
    "extract_from_pdf_urls",
]
