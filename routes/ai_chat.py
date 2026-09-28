"""
routes/ai_chat.py
-----------------
POST /api/ai/chat  — the only endpoint the AI Copilot UI calls.

Security:
- login_required (authenticated users only)
- organization_id resolved server-side from current_user (never from frontend)
- CSRF protected (uses JSON + flask-wtf exemption via X-CSRFToken header)
- No stack traces exposed to client
- AI is read-only
"""

import logging
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)

ai_chat_bp = Blueprint("ai_chat", __name__, url_prefix="/api/ai")


@ai_chat_bp.route("/chat", methods=["POST"])
@login_required
def chat():
    # ── Resolve org from authenticated session — NEVER from frontend ──
    org_id = current_user.organization_id
    if not org_id:
        return jsonify({"success": False, "error": "No organization associated with your account."}), 403

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "Invalid request body."}), 400

    question = (data.get("message") or "").strip()
    if not question:
        return jsonify({"success": False, "error": "Please provide a message."}), 400

    if len(question) > 1000:
        return jsonify({"success": False, "error": "Question is too long (max 1000 characters)."}), 400

    try:
        from ai.ai_service import process_question
        result = process_question(question=question, org_id=org_id, current_user_id=current_user.id)
        return jsonify({
            "success": True,
            "answer": result["answer"],
            "sources": result.get("sources", []),
            "type": result.get("query_type", "GENERAL"),
            "structured_data": result.get("structured_data"),
            "document_data": result.get("document_data", [])
        })
    except Exception as e:
        logger.error(f"AI chat error for org={org_id}: {e}", exc_info=True)
        # Never expose internal errors to the client
        return jsonify({
            "success": True,
            "answer": "AI Copilot is temporarily unavailable. Please try again later.",
            "sources": [],
            "type": "ERROR",
        })


@ai_chat_bp.route("/status", methods=["GET"])
@login_required
def status():
    """Health check for the AI backend — returns availability info."""
    import os
    ai_enabled = os.environ.get("AI_ENABLED", "true").lower() == "true"
    llm_available = False

    if ai_enabled:
        try:
            from ai.llm_provider import get_llm
            llm = get_llm()
            llm_available = llm._is_healthy()
        except Exception:
            pass

    return jsonify({
        "ai_enabled": ai_enabled,
        "llm_available": llm_available,
        "model": os.environ.get("AI_MODEL", "mistral"),
    })


@ai_chat_bp.route("/index-document", methods=["POST"])
@login_required
def index_document():
    """
    Trigger indexing of an already-uploaded CRM document.
    Body: {source_type, source_id, file_path, original_name}
    Only accessible to authenticated org members.
    """
    org_id = current_user.organization_id
    if not org_id:
        return jsonify({"success": False, "error": "No organization."}), 403

    data = request.get_json(silent=True) or {}
    source_type   = data.get("source_type", "")
    source_id     = data.get("source_id")
    file_path     = data.get("file_path", "")
    original_name = data.get("original_name", "")

    if not all([source_type, source_id, file_path]):
        return jsonify({"success": False, "error": "Missing required fields."}), 400

    # Security: ensure the file path is within the upload folder
    import os
    from flask import current_app
    upload_root = current_app.config.get("UPLOAD_FOLDER", "")
    abs_path = os.path.abspath(file_path)
    abs_upload = os.path.abspath(upload_root)
    if not abs_path.startswith(abs_upload):
        return jsonify({"success": False, "error": "Invalid file path."}), 400

    try:
        from ai.document_indexer import index_document as _index
        count = _index(
            org_id=org_id,
            file_path=abs_path,
            source_type=source_type,
            source_id=int(source_id),
            original_name=original_name,
        )
        return jsonify({"success": True, "chunks_indexed": count})
    except Exception as e:
        logger.error(f"Document indexing error: {e}")
        return jsonify({"success": False, "error": "Indexing failed."}), 500
