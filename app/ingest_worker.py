"""
Async ingest worker - processes items from ingest_queue.

Runs the full ingest pipeline (PDF parse, trafilatura, embedding, deduplication)
in the background. Called by BackgroundTasks and by the scheduler for retries.
"""
import logging
from datetime import datetime
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Document, IngestQueue
from .schemas import IngestPayload
from .helpers import embed_doc_chunks

logger = logging.getLogger(__name__)


def _format_linked_pdfs_as_markdown(pdf_urls: list, exclude: str = None) -> str:
    """Format PDF URLs as markdown links for reference."""
    if not pdf_urls:
        return ""
    refs = []
    for url in pdf_urls:
        if url == exclude:
            continue
        if '/' in url:
            filename = url.split('/')[-1]
            if '?' in filename:
                filename = filename.split('?')[0]
        else:
            filename = url[:50] + "..." if len(url) > 50 else url
        refs.append(f"- [{filename}]({url})")
    return "\n".join(refs)


def _extract_arxiv_id(url: str) -> str:
    """Extract arXiv paper ID from URL."""
    import re
    match = re.search(r'arxiv\.org/(?:abs|pdf)/([^/?#]+?)(?:\.pdf)?$', url)
    return match.group(1) if match else None


def _normalize_pdf_url(url: str) -> str:
    """Normalize PDF URL for extraction."""
    if not url:
        return url
    if 'arxiv.org/pdf/' in url and not url.endswith('.pdf'):
        paper_id = _extract_arxiv_id(url)
        if paper_id:
            return f"https://arxiv.org/pdf/{paper_id}.pdf"
    return url


def _is_pdf_url(url: str) -> bool:
    """Check if URL is a direct PDF."""
    if not url:
        return False
    url_lower = url.lower()
    return (
        url_lower.endswith('.pdf') or
        '/pdf/' in url_lower or
        url_lower.endswith('/pdf')
    )


def _payload_from_json(payload_json: dict) -> IngestPayload:
    """Parse payload_json dict back to IngestPayload."""
    return IngestPayload.model_validate(payload_json)


def process_ingest_item(queue_id: str) -> None:
    """
    Process a single ingest queue item: PDF parse, trafilatura, create doc, embed, dedupe.

    Updates queue row status on success/failure. Does not raise - logs errors.
    """
    db: Session = SessionLocal()
    try:
        queue_row = db.query(IngestQueue).filter(IngestQueue.id == queue_id).first()
        if not queue_row:
            logger.warning(f"Ingest queue item {queue_id} not found")
            return
        if queue_row.status not in ("pending", "processing"):
            logger.info(f"Ingest queue item {queue_id} already {queue_row.status}, skipping")
            return

        queue_row.status = "processing"
        queue_row.processing_started_at = datetime.utcnow()
        db.commit()

        payload = _payload_from_json(queue_row.payload_json)
        user_id = queue_row.user_id
        vault_id = queue_row.vault_id

        captured_at = payload.captured_at or datetime.utcnow()
        captured_text = payload.text or ""
        title = payload.title
        extracted_text = None
        pdf_parsed = False
        pdf_to_parse = payload.pdf_to_parse

        if not pdf_to_parse and payload.url:
            if _is_pdf_url(payload.url):
                pdf_to_parse = payload.url
            elif 'arxiv.org/abs/' in payload.url:
                paper_id = _extract_arxiv_id(payload.url)
                if paper_id:
                    pdf_to_parse = f"https://arxiv.org/pdf/{paper_id}.pdf"

        if not pdf_to_parse and payload.pdf_urls and len(payload.pdf_urls) > 0:
            if _is_pdf_url(payload.url):
                pdf_to_parse = payload.url

        if pdf_to_parse:
            pdf_to_parse = _normalize_pdf_url(pdf_to_parse)
            logger.info(f"Parsing PDF as primary content: {pdf_to_parse}")
            try:
                from .extraction import extract_from_pdf_urls
                pdf_text = extract_from_pdf_urls([pdf_to_parse], max_pages_per_pdf=50)
                if pdf_text:
                    captured_text = pdf_text
                    pdf_parsed = True
                    logger.info(f"PDF parsed successfully: {len(pdf_text)} chars")
                else:
                    logger.warning(f"No text extracted from PDF: {pdf_to_parse}")
            except Exception as e:
                logger.error(f"Failed to parse PDF {pdf_to_parse}: {e}", exc_info=True)

        linked_pdfs = payload.linked_pdf_urls or []
        if not linked_pdfs and payload.pdf_urls:
            linked_pdfs = payload.pdf_urls

        if linked_pdfs:
            refs = _format_linked_pdfs_as_markdown(linked_pdfs, exclude=pdf_to_parse)
            if refs:
                captured_text += f"\n\n---\n\n**Linked Documents:**\n{refs}"

        if payload.mode == "page" and not pdf_parsed and payload.url and not payload.url.startswith("note://"):
            try:
                from .extraction import extract_from_url
                extraction_result = extract_from_url(payload.url)
                if extraction_result and extraction_result.get("text"):
                    extracted_text = extraction_result["text"]
                    if not payload.title and extraction_result.get("title"):
                        title = extraction_result.get("title")
            except Exception as e:
                logger.warning(f"Trafilatura extraction error (non-fatal): {e}")

        if not captured_text or not captured_text.strip():
            queue_row.status = "failed"
            queue_row.error_message = "No text content found after extraction"
            db.commit()
            logger.error(f"Ingest {queue_id} failed: no text content")
            return

        has_extracted_text = extracted_text is not None

        if has_extracted_text and vault_id:
            from .topics import find_url_duplicate
            existing_doc = find_url_duplicate(db, vault_id, payload.url, has_extracted_text)
            if existing_doc:
                queue_row.status = "completed"
                queue_row.document_id = existing_doc.id
                db.commit()
                logger.info(f"Ingest {queue_id} duplicate (URL), existing doc {existing_doc.id}")
                return

        doc = Document(
            vault_id=vault_id,
            created_by=user_id,
            url=payload.url,
            title=title,
            captured_text=captured_text,
            extracted_text=extracted_text,
            full_text=captured_text,
            captured_at=captured_at,
        )
        db.add(doc)
        db.commit()

        embed_doc_chunks(doc)

        if has_extracted_text and vault_id:
            from .topics import find_semantic_duplicate
            existing_doc = find_semantic_duplicate(db, doc)
            if existing_doc:
                db.delete(doc)
                db.commit()
                queue_row.status = "completed"
                queue_row.document_id = existing_doc.id
                db.commit()
                logger.info(f"Ingest {queue_id} duplicate (semantic), existing doc {existing_doc.id}")
                return

        queue_row.status = "completed"
        queue_row.document_id = doc.id
        db.commit()
        logger.info(f"Ingest {queue_id} completed, document {doc.id}")

    except Exception as e:
        logger.error(f"Ingest {queue_id} failed: {e}", exc_info=True)
        try:
            queue_row = db.query(IngestQueue).filter(IngestQueue.id == queue_id).first()
            if queue_row:
                queue_row.status = "failed"
                queue_row.error_message = str(e)[:500]
                db.commit()
        except Exception as inner:
            logger.error(f"Failed to update queue status: {inner}")
    finally:
        db.close()


def process_pending_ingest_queue() -> dict:
    """
    Process all pending (and stuck processing) items in the ingest queue.

    Called by the scheduler. Resets items stuck in 'processing' for > 10 minutes.
    Returns stats dict.
    """
    from datetime import timedelta

    db: Session = SessionLocal()
    stats = {"processed": 0, "reset_stuck": 0, "errors": 0}
    try:
        stuck_threshold = datetime.utcnow() - timedelta(minutes=10)
        stuck = db.query(IngestQueue).filter(
            IngestQueue.status == "processing",
            IngestQueue.processing_started_at < stuck_threshold
        ).all()
        for row in stuck:
            row.status = "pending"
            row.processing_started_at = None
            stats["reset_stuck"] += 1
        if stuck:
            db.commit()

        pending = db.query(IngestQueue).filter(IngestQueue.status == "pending").order_by(IngestQueue.created_at).limit(10).all()
        for row in pending:
            try:
                process_ingest_item(str(row.id))
                stats["processed"] += 1
            except Exception as e:
                logger.error(f"Error processing ingest {row.id}: {e}")
                stats["errors"] += 1

        return stats
    finally:
        db.close()
