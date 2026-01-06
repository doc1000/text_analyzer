# app/db.py
import os
#import time
#from typing import Optional, Tuple
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker #, Session
from .models import Base
#from .config import PREFERENCES

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://badger:badgerpass@db:5432/badgerdb")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
EMBED_TABLE = {}
def ensure_schemas():
    with engine.connect() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS embedding"))
        conn.commit()


def init_db():
    ensure_schemas()
    
    Base.metadata.create_all(bind=engine)
    # After Base.metadata.create_all(bind=engine)
    # This will add the embedding table to the embedding.embedding_model table
    """
    All tables are defined using SQLAlchemy’s declarative base, suitable for Base.metadata.create_all(bind=engine).
    Each embedding model gets its own table under the embedding schema and a row in embedding.embedding_model.
    Your config.py can pick the model, look up the meta row, and you can map table_location back to the corresponding dynamic class if needed (e.g., via a registry dict keyed by (model_name, version)).
    """
    #with Session(engine) as session:
    #    register_embedding_model(session, "bge-small-en", "v1.5", 512)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()





