"""
ai/document_indexer.py
----------------------
Extracts text from CRM documents, splits into chunks,
generates embeddings, and stores in the vector store.

Supported formats: PDF, TXT, plain text.
organization_id is required for all operations — tenant isolation.
"""

import os
import re
import logging
import hashlib
from typing import Optional

logger = logging.getLogger(__name__)

CHUNK_SIZE    = 400   # characters per chunk
CHUNK_OVERLAP = 80    # overlap between adjacent chunks


# ── Text Extraction ────────────────────────────────────────

def extract_text_from_file(file_path: str) -> Optional[str]:
    """Extract text from a supported file. Returns None if unsupported/unreadable."""
    if not os.path.exists(file_path):
        logger.warning(f"File not found: {file_path}")
        return None

    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        return _extract_pdf(file_path)
    elif ext in (".txt", ".md", ".csv"):
        return _extract_txt(file_path)
    else:
        logger.debug(f"Unsupported file type for RAG: {ext}")
        return None


def _extract_pdf(path: str) -> Optional[str]:
    try:
        import PyPDF2
        text = []
        with open(path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    text.append(t)
        return "\n".join(text).strip() or None
    except Exception as e:
        logger.error(f"PDF extraction failed for {path}: {e}")
        return None


def _extract_txt(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip() or None
    except Exception as e:
        logger.error(f"TXT extraction failed for {path}: {e}")
        return None


# ── Text Splitting ─────────────────────────────────────────

def _clean_text(text: str) -> str:
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def split_into_chunks(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
    text = _clean_text(text)
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap
        if start >= len(text):
            break
    return chunks


# ── Main Indexing Functions ────────────────────────────────

def index_document(
    org_id: int,
    file_path: str,
    source_type: str,   # e.g. "quotation_attachment", "customer_document", "crm_document"
    source_id: int,     # the record ID in the CRM table
    document_id: Optional[int] = None,
    original_name: Optional[str] = None,
) -> int:
    """
    Index a single document file into the vector store.
    Returns the number of chunks indexed, or 0 on failure.
    """
    text = extract_text_from_file(file_path)
    if not text:
        return 0

    chunks = split_into_chunks(text)
    if not chunks:
        return 0

    # Import here to avoid circular imports and heavy loading at startup
    from ai.embeddings import embed_texts
    from ai.vector_store import upsert_chunks, content_hash

    embeddings = embed_texts(chunks)

    metadata = []
    for i, chunk in enumerate(chunks):
        metadata.append({
            "org_id": org_id,
            "source_type": source_type,
            "source_id": source_id,
            "document_id": document_id,
            "chunk_index": i,
            "filename": original_name or os.path.basename(file_path),
            "chunk_hash": content_hash(chunk),
        })

    count = upsert_chunks(org_id, chunks, embeddings, metadata)
    logger.info(f"Indexed {count} chunks for org={org_id} source={source_type}:{source_id}")
    return count


def delete_document_chunks(org_id: int, source_type: str, source_id: int) -> int:
    """Remove all chunks for a deleted CRM document."""
    from ai.vector_store import delete_chunks_by_source
    removed = delete_chunks_by_source(org_id, source_type, source_id)
    logger.info(f"Removed {removed} chunks for org={org_id} source={source_type}:{source_id}")
    return removed


def reindex_document(
    org_id: int,
    file_path: str,
    source_type: str,
    source_id: int,
    document_id: Optional[int] = None,
    original_name: Optional[str] = None,
) -> int:
    """Delete existing chunks then re-index (use when a document is updated)."""
    delete_document_chunks(org_id, source_type, source_id)
    return index_document(org_id, file_path, source_type, source_id, document_id, original_name)
