"""
ai/embeddings.py
----------------
Local embedding provider using sentence-transformers.
Model: all-MiniLM-L6-v2 (~22 MB, CPU-friendly, great quality).
No internet call at inference time once the model is cached.
"""

import os
import logging
from typing import List

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = os.environ.get("AI_EMBEDDING_MODEL", "all-MiniLM-L6-v2")

_encoder = None


def get_encoder():
    """Lazy-load the sentence-transformer model (only on first call)."""
    global _encoder
    if _encoder is None:
        try:
            from sentence_transformers import SentenceTransformer
            logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
            _encoder = SentenceTransformer(EMBEDDING_MODEL)
            logger.info("Embedding model loaded.")
        except ImportError:
            logger.error("sentence-transformers not installed. Run: pip install sentence-transformers")
            raise
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            raise
    return _encoder


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Return a list of embedding vectors for the given texts."""
    encoder = get_encoder()
    embeddings = encoder.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    return embeddings.tolist()


def embed_text(text: str) -> List[float]:
    """Return a single embedding vector."""
    return embed_texts([text])[0]
