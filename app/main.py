# save as main.py
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import spacy
#import zlib
import gzip
import math
import uvicorn
from .db import SessionLocal

nlp = spacy.load("en_core_web_sm")

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Allow extension + localhost
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # for dev, you can later restrict this
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/test-db")
def test_db(db: Session = Depends(get_db)):
    # quick sanity query
    result = db.execute("SELECT 1;")
    return {"db_ok": bool(list(result))}

class Input(BaseModel):
    text: str

@app.post("/analyze")
def analyze(data: Input):
    text = data.text
    doc = nlp(text)
    
    # Lexical density
    content_words = [t for t in doc if t.pos_ in ["NOUN","VERB","ADJ","ADV"]]
    lexical_density = len(content_words) / len([t for t in doc if t.is_alpha]) if text else 0

    # Specificity (rough)
    numbers = sum(1 for t in doc if t.like_num)
    entities = len(doc.ents)
    sents = list(doc.sents)
    specificity = min(1.0, (numbers + entities) / (len(sents) + 1))

    # Compression ratio
    compressed = len(gzip.compress(text.encode("utf-8")))
    #compressed = len(zlib.compress(text.encode("utf-8")))
    ratio = len(text.encode("utf-8")) / compressed if compressed else 1
    
    # Combine scores (weights adjustable)
    score = (
        lexical_density * 40 +
        specificity * 40 +
        ratio * 20
    )
    score = min(100, score)

    return {
        "score": score,
        "lexical_density": lexical_density,
        "specificity": specificity,
        "compression_ratio": ratio
    }

if __name__ == "__main__":
	#pip install -r requirements.txt
	uvicorn.run(app, host="127.0.0.1", port=8000)
