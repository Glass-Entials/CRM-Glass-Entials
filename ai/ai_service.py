"""
ai/ai_service.py
----------------
Top-level AI Copilot service.
Orchestrates: query routing → CRM query → document retrieval → context building → LLM response.

This is the only module that routes.ai_chat.py should call.
"""

import logging
from typing import Optional

from ai.query_router import classify_query
from ai.crm_query import (
    get_customers, count_customers,
    get_leads, count_leads,
    get_quotations, count_quotations,
    get_invoices, count_invoices,
    get_tasks, get_overdue_tasks,
    get_projects,
    get_recent_activities,
    get_monthly_quotation_summary,
)
from ai.context_builder import build_context, build_llm_messages

logger = logging.getLogger(__name__)

MAX_DOC_CHUNKS = 4
MAX_RECORDS    = 8


def _run_structured_query(question: str, org_id: int) -> Optional[dict]:
    """Map the user question to a controlled CRM query. Returns {type, data, summary} or None."""
    q = question.lower()

    if any(w in q for w in ["overdue task", "overdue tasks"]):
        data = get_overdue_tasks(org_id)
        return {"type": "tasks", "data": data, "summary": f"{len(data)} overdue task(s)"}

    if any(w in q for w in ["pending task", "pending tasks", "my tasks", "show tasks"]):
        data = get_tasks(org_id, limit=MAX_RECORDS, status="Pending")
        return {"type": "tasks", "data": data, "summary": f"{len(data)} pending task(s)"}

    if "task" in q:
        data = get_tasks(org_id, limit=MAX_RECORDS)
        return {"type": "tasks", "data": data}

    if any(w in q for w in ["unpaid invoice", "unpaid invoices", "pending invoice"]):
        data = get_invoices(org_id, limit=MAX_RECORDS, paid=False)
        count = count_invoices(org_id, paid=False)
        return {"type": "invoices", "data": data, "summary": f"{count} unpaid invoice(s)"}

    if "invoice" in q:
        data = get_invoices(org_id, limit=MAX_RECORDS)
        count = count_invoices(org_id)
        return {"type": "invoices", "data": data, "summary": f"{count} total invoice(s)"}

    if any(w in q for w in ["pending quotation", "sent quotation", "quotation this month", "quotation value"]):
        summary = get_monthly_quotation_summary(org_id)
        data = get_quotations(org_id, limit=MAX_RECORDS)
        return {"type": "quotations", "data": data, "summary": f"This month: {summary['count']} quotation(s), total ₹{summary['total_amount']:,.2f}"}

    if "quotation" in q:
        data = get_quotations(org_id, limit=MAX_RECORDS)
        count = count_quotations(org_id)
        return {"type": "quotations", "data": data, "summary": f"{count} total quotation(s)"}

    if any(w in q for w in ["how many customer", "count customer", "number of customer"]):
        count = count_customers(org_id)
        return {"type": "customers", "data": count, "summary": f"Total customers: {count}"}

    if any(w in q for w in ["customer", "clients", "client"]):
        data = get_customers(org_id, limit=MAX_RECORDS)
        count = count_customers(org_id)
        return {"type": "customers", "data": data, "summary": f"{count} total customer(s)"}

    if any(w in q for w in ["how many lead", "count lead"]):
        count = count_leads(org_id)
        return {"type": "leads", "data": count, "summary": f"Total leads: {count}"}

    if "lead" in q:
        data = get_leads(org_id, limit=MAX_RECORDS)
        count = count_leads(org_id)
        return {"type": "leads", "data": data, "summary": f"{count} total lead(s)"}

    if "project" in q:
        data = get_projects(org_id, limit=MAX_RECORDS)
        return {"type": "projects", "data": data}

    if any(w in q for w in ["activity", "activities", "recent activity", "today"]):
        data = get_recent_activities(org_id, limit=MAX_RECORDS)
        return {"type": "activities", "data": data}

    return None


def _run_document_search(question: str, org_id: int):
    """Search the vector store for relevant document chunks."""
    try:
        from ai.embeddings import embed_text
        from ai.vector_store import search
        q_emb = embed_text(question)
        results = search(org_id, q_emb, top_k=MAX_DOC_CHUNKS)
        return results
    except ImportError:
        logger.warning("sentence-transformers not installed; skipping document search.")
        return []
    except Exception as e:
        logger.error(f"Document search failed: {e}")
        return []


def process_question(question: str, org_id: int) -> dict:
    """
    Main entry point.
    Returns: {answer, sources, query_type, error}
    """
    if not question or not question.strip():
        return {"answer": "Please ask a question.", "sources": [], "query_type": "NONE"}

    question = question.strip()
    query_type = classify_query(question)

    structured_result = None
    doc_chunks = []

    # Run structured query for STRUCTURED or HYBRID
    if query_type in ("STRUCTURED", "HYBRID"):
        try:
            structured_result = _run_structured_query(question, org_id)
        except Exception as e:
            logger.error(f"Structured query error: {e}")

    # Run document search for DOCUMENT or HYBRID
    if query_type in ("DOCUMENT", "HYBRID"):
        doc_chunks = _run_document_search(question, org_id)

    # Build context
    context_text, sources = build_context(structured_result, doc_chunks, question)

    # If no context and type is STRUCTURED, give a helpful message directly
    if not context_text.strip() and query_type == "STRUCTURED":
        return {
            "answer": "I couldn't find relevant CRM data for that query. Try being more specific, e.g., 'Show my pending tasks' or 'How many customers do we have?'",
            "sources": [],
            "query_type": query_type,
        }

    # For pure STRUCTURED queries, skip the LLM to guarantee deterministic card rendering and save time
    if query_type == "STRUCTURED" and structured_result:
        answer = structured_result.get("summary", "Here is the data from your CRM.")
    else:
        # Call local LLM for GENERAL, DOCUMENT, and HYBRID
        try:
            from ai.llm_provider import get_llm, LLMUnavailableError
            llm = get_llm()
            messages = build_llm_messages(question, context_text)
            answer = llm.chat(messages)
        except Exception as e:
            # LLM unavailable — return structured context directly as plain text if available
            logger.warning(f"LLM unavailable: {e}")
            if structured_result:
                answer = _format_fallback_answer(structured_result, question)
            else:
                answer = "AI Copilot is temporarily unavailable. The local AI model is not running. Please start Ollama: `ollama serve`"

    return {
        "answer": answer,
        "sources": sources,
        "query_type": query_type,
        "structured_data": structured_result,
        "document_data": doc_chunks if doc_chunks else []
    }


def _format_fallback_answer(structured_result: dict, question: str) -> str:
    """Return a plain-text formatted answer when the LLM is unavailable."""
    summary = structured_result.get("summary", "")
    data = structured_result.get("data")
    qtype = structured_result.get("type", "data")

    lines = []
    if summary:
        lines.append(summary)

    if isinstance(data, list):
        for record in data[:5]:
            parts = []
            for k, v in record.items():
                if v is not None and k not in ("id",):
                    parts.append(f"{k.replace('_',' ').title()}: {v}")
            lines.append("• " + " | ".join(parts))
    elif isinstance(data, (int, float)):
        lines.append(f"Count: {data}")

    if lines:
        return "\n".join(lines)
    return f"Found {qtype} data but could not format it. Please start the local AI model for better responses."
