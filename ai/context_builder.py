"""
ai/context_builder.py
---------------------
Builds a compact, structured context string for the local LLM.
Combines structured CRM query results and retrieved document chunks.
"""

import json
from typing import List, Dict, Any, Optional


SYSTEM_PROMPT = """You are GlassEntials AI Copilot — an assistant for the GlassEntials CRM.

Rules you must strictly follow:
1. Answer using ONLY the CRM data provided in the context below.
2. If the information is not in the context, say "I don't have that information in your CRM."
3. NEVER invent customer names, invoice numbers, quotation totals, dates, or payment status.
4. NEVER claim an action was performed unless the backend actually performed it.
5. You are READ-ONLY. You cannot modify, delete, or create any CRM records.
6. For general knowledge questions (not CRM-specific), clearly state you are answering from general knowledge.
7. Keep answers concise, professional, and business-appropriate.
8. Format numbers and currency clearly (e.g., ₹1,18,000).
"""


def format_structured_context(query_results: Dict[str, Any]) -> str:
    """
    Turn structured CRM data dict into a readable context block.
    query_results: {"type": "customers", "data": [...]}
    """
    if not query_results:
        return ""

    lines = ["\n--- CRM DATA ---"]
    qtype = query_results.get("type", "")
    data  = query_results.get("data")

    if isinstance(data, list):
        for record in data:
            lines.append(_format_record(record))
    elif isinstance(data, dict):
        lines.append(_format_record(data))
    elif data is not None:
        lines.append(str(data))

    if "summary" in query_results:
        lines.append(f"Summary: {query_results['summary']}")

    return "\n".join(lines)


def _format_record(record: dict) -> str:
    parts = []
    for k, v in record.items():
        if v is not None and v != "":
            key_label = k.replace("_", " ").title()
            parts.append(f"{key_label}: {v}")
    return " | ".join(parts)


def format_document_context(doc_chunks: List[Dict]) -> str:
    """Format retrieved document chunks."""
    if not doc_chunks:
        return ""

    lines = ["\n--- DOCUMENT CONTEXT ---"]
    for i, chunk in enumerate(doc_chunks, 1):
        meta = chunk.get("metadata", {})
        source = meta.get("filename") or meta.get("source_type", "document")
        lines.append(f"[Source {i}: {source}]")
        lines.append(chunk["content"])
    return "\n".join(lines)


def build_context(
    structured_results: Optional[Dict] = None,
    doc_chunks: Optional[List[Dict]] = None,
    question: str = "",
) -> tuple[str, list]:
    """
    Returns (context_text, sources_list).
    context_text will be included in the LLM prompt.
    sources_list will be returned to the frontend.
    """
    context_parts = []
    sources = []

    if structured_results:
        ctx = format_structured_context(structured_results)
        if ctx:
            context_parts.append(ctx)
            data = structured_results.get("data")
            if isinstance(data, list):
                for record in data:
                    label = record.get("name") or record.get("quotation_number") or \
                            record.get("invoice_number") or record.get("title", "")
                    if label:
                        sources.append({
                            "type": structured_results.get("type", "record"),
                            "label": label,
                        })

    if doc_chunks:
        ctx = format_document_context(doc_chunks)
        if ctx:
            context_parts.append(ctx)
            for chunk in doc_chunks:
                meta = chunk.get("metadata", {})
                filename = meta.get("filename", "Document")
                if not any(s.get("label") == filename for s in sources):
                    sources.append({"type": "document", "label": filename})

    context_text = "\n".join(context_parts)
    return context_text, sources


def build_llm_messages(question: str, context_text: str) -> list:
    """Build the message list for the LLM chat call."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if context_text.strip():
        user_content = f"Context:\n{context_text}\n\nQuestion: {question}"
    else:
        user_content = f"Question: {question}"

    messages.append({"role": "user", "content": user_content})
    return messages
