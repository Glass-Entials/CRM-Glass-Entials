"""
ai/query_router.py
------------------
Classifies an incoming user question into one of four types:
  STRUCTURED  - query against live MySQL CRM data
  DOCUMENT    - search indexed document chunks
  HYBRID      - both structured + document
  GENERAL     - general knowledge (no CRM data needed)

Uses deterministic keyword rules first. Falls back to LLM classification only
if rules are inconclusive (which is rare given the CRM domain).
"""

import re
from typing import Literal

QueryType = Literal["STRUCTURED", "DOCUMENT", "HYBRID", "GENERAL"]


# ── keyword lists ─────────────────────────────────────────
_STRUCTURED_KEYWORDS = re.compile(
    r"\b(how many|count|total|number of|list|show|find|get|pending|overdue|unpaid|paid|recent|"
    r"latest|customers?|leads?|projects?|tasks?|quotations?|invoices?|activities?|summary|this month|"
    r"today|yesterday|this week)\b",
    re.IGNORECASE,
)

_DOCUMENT_KEYWORDS = re.compile(
    r"\b(document|pdf|attachment|file|uploaded|scanned|letter|contract|"
    r"terms? of|payment terms?|warranty|warranty terms?|scope of work|specification)\b",
    re.IGNORECASE,
)

_GENERAL_KEYWORDS = re.compile(
    r"\b(what is|explain|define|tell me about|how does|difference between|meaning of|"
    r"hello|hi|who are you|what can you do)\b",
    re.IGNORECASE,
)


def classify_query(question: str) -> QueryType:
    q = question.strip().lower()

    has_structured = bool(_STRUCTURED_KEYWORDS.search(q))
    has_document   = bool(_DOCUMENT_KEYWORDS.search(q))
    has_general    = bool(_GENERAL_KEYWORDS.search(q))

    if has_document and has_structured:
        return "HYBRID"
    if has_document:
        return "DOCUMENT"
    if has_structured:
        return "STRUCTURED"
    if has_general:
        return "GENERAL"

    # Short queries without clear signals default to structured (most useful in CRM context)
    if len(q.split()) <= 6:
        return "STRUCTURED"

    return "GENERAL"
