"""
ai/vector_store.py
------------------
Lightweight numpy-based vector store.
No native C++ extensions required.
Persists to a single .npz file per organization (tenant-isolated).

Format per org:
  data/<org_id>/vectors.npz  →  arrays: embeddings, contents, metas (as JSON strings)
"""

import os
import json
import hashlib
import logging
from typing import List, Dict, Optional, Any

import numpy as np

logger = logging.getLogger(__name__)

_STORE_BASE = os.path.join(os.path.dirname(__file__), "..", "instance", "ai_vectors")


def _org_path(org_id: int) -> str:
    d = os.path.join(_STORE_BASE, str(org_id))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "vectors.npz")


def _load(org_id: int):
    path = _org_path(org_id)
    if not os.path.exists(path):
        return np.empty((0, 384), dtype=np.float32), [], []

    data = np.load(path, allow_pickle=True)
    embeddings = data["embeddings"]
    contents   = data["contents"].tolist()
    metas      = [json.loads(m) for m in data["metas"].tolist()]
    return embeddings, contents, metas


def _save(org_id: int, embeddings, contents: list, metas: list):
    path = _org_path(org_id)
    np.savez_compressed(
        path,
        embeddings=np.array(embeddings, dtype=np.float32),
        contents=np.array(contents, dtype=object),
        metas=np.array([json.dumps(m) for m in metas], dtype=object),
    )


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def upsert_chunks(
    org_id: int,
    chunks: List[str],
    embeddings: List[List[float]],
    metadata: List[Dict[str, Any]],
):
    """
    Insert or replace chunks for a specific source.
    Deduplication key: metadata["chunk_hash"].
    """
    existing_emb, existing_contents, existing_metas = _load(org_id)

    # Build lookup of existing chunk hashes
    existing_hashes = {m.get("chunk_hash") for m in existing_metas}

    new_emb, new_content, new_meta = [], [], []
    for chunk, emb, meta in zip(chunks, embeddings, metadata):
        ch = meta.get("chunk_hash")
        if ch and ch in existing_hashes:
            continue  # already indexed
        new_emb.append(emb)
        new_content.append(chunk)
        new_meta.append(meta)

    if not new_emb:
        return 0

    if len(existing_emb) > 0:
        all_emb = np.vstack([existing_emb, np.array(new_emb, dtype=np.float32)])
        all_content = existing_contents + new_content
        all_meta = existing_metas + new_meta
    else:
        all_emb = np.array(new_emb, dtype=np.float32)
        all_content = new_content
        all_meta = new_meta

    _save(org_id, all_emb, all_content, all_meta)
    return len(new_emb)


def delete_chunks_by_source(org_id: int, source_type: str, source_id: int):
    """Remove all chunks belonging to a specific CRM record."""
    existing_emb, existing_contents, existing_metas = _load(org_id)
    if len(existing_metas) == 0:
        return 0

    keep_idx = [
        i for i, m in enumerate(existing_metas)
        if not (m.get("source_type") == source_type and m.get("source_id") == source_id)
    ]
    removed = len(existing_metas) - len(keep_idx)
    if removed == 0:
        return 0

    _save(
        org_id,
        existing_emb[keep_idx],
        [existing_contents[i] for i in keep_idx],
        [existing_metas[i] for i in keep_idx],
    )
    return removed


def search(
    org_id: int,
    query_embedding: List[float],
    top_k: int = 5,
    source_type_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Cosine similarity search.
    ALWAYS scoped to org_id — tenant isolation enforced here.
    Returns [{content, score, metadata}, ...]
    """
    embeddings, contents, metas = _load(org_id)
    if len(embeddings) == 0:
        return []

    q = np.array(query_embedding, dtype=np.float32)
    q_norm = q / (np.linalg.norm(q) + 1e-10)

    norms = np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-10
    normed = embeddings / norms
    scores = normed @ q_norm

    if source_type_filter:
        # Zero out scores for non-matching source types
        for i, m in enumerate(metas):
            if m.get("source_type") != source_type_filter:
                scores[i] = -1.0

    idx = np.argsort(scores)[::-1][:top_k]
    results = []
    for i in idx:
        if scores[i] > 0.2:  # minimum relevance threshold
            results.append({
                "content": contents[i],
                "score": float(scores[i]),
                "metadata": metas[i],
            })
    return results
