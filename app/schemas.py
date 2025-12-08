# app/schemas.py
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

# --- Ingest payload from extension ---
class IngestPayload(BaseModel):
    url: str
    title: Optional[str] = None
    text: str
    score_info: Optional[float] = None
    score_ai_slop: Optional[float] = None
    captured_at: Optional[datetime] = None