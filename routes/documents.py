import os
import uuid
from flask import (
    Blueprint,
    request,
    flash,
    redirect,
    url_for,
    send_from_directory,
    current_app,
    abort,
)
from flask_login import login_required, current_user
from model import db, CRMDocument, Lead, Project, Task, DailyTask, TaskActivity
from werkzeug.utils import secure_filename
from utils.security import tenant_record_id, safe_redirect_target

documents_bp = Blueprint("documents", __name__)
ENTITY_MODELS = {
    "lead": Lead,
    "project": Project,
    "task": Task,
    "daily_task": DailyTask,
    "task_activity": TaskActivity,
}

ALLOWED_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "txt",
}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@documents_bp.route("/upload-document", methods=["POST"])
@login_required
def upload_document():
    entity_type = request.form.get("entity_type")
    entity_id = request.form.get("entity_id")
    description = request.form.get("description", "")

    if not entity_type or not entity_id:
        flash("Missing entity information.", "error")
        return redirect(safe_redirect_target(request.referrer, url_for("home_page")))

    model = ENTITY_MODELS.get(entity_type)
    if not model:
        flash("Invalid document target.", "error")
        return redirect(safe_redirect_target(request.referrer, url_for("home_page")))

    entity_id = tenant_record_id(
        model, entity_id, current_user.organization_id, allow_none=False
    )

    if "file" not in request.files:
        flash("No file part.", "error")
        return redirect(safe_redirect_target(request.referrer, url_for("home_page")))

    file = request.files["file"]
    if file.filename == "":
        flash("No selected file.", "error")
        return redirect(safe_redirect_target(request.referrer, url_for("home_page")))

    from utils.documents import handle_file_upload

    success = handle_file_upload(
        file=file,
        entity_type=entity_type,
        entity_id=entity_id,
        organization_id=current_user.organization_id,
        uploader_id=current_user.employee.id if current_user.employee else None,
        description=description,
    )

    if success:
        db.session.commit()
        flash("Document uploaded successfully!", "success")
    else:
        flash("File type not allowed or upload failed.", "error")

    return redirect(safe_redirect_target(request.referrer, url_for("home_page")))


from flask import send_file


@documents_bp.route("/download-document/<int:doc_id>")
@login_required
def download_document(doc_id):
    try:
        # Enforce organization isolation inline
        doc = CRMDocument.query.filter_by(
            id=doc_id, organization_id=current_user.organization_id
        ).first_or_404()

        # Build absolute path
        upload_folder = current_app.config.get("UPLOAD_FOLDER")
        if not upload_folder:
            upload_folder = os.path.join(current_app.root_path, "static", "uploads")

        file_path = os.path.join(upload_folder, "crm_docs", doc.filename)
        file_path = os.path.normpath(file_path)

        if not os.path.exists(file_path):
            current_app.logger.warning(f"File missing on disk: doc_id={doc_id}")
            flash("File not found on server storage.", "error")
            return redirect(safe_redirect_target(request.referrer, url_for("tasks.daily_tasks_list")))

        import mimetypes
        mime_type, _ = mimetypes.guess_type(doc.original_name)
        ext = doc.original_name.lower().split('.')[-1] if '.' in doc.original_name else ''
        if ext == 'pdf':
            mime_type = 'application/pdf'
        elif ext in ['jpg', 'jpeg']:
            mime_type = 'image/jpeg'
        elif ext == 'png':
            mime_type = 'image/png'
            
        return send_file(
            file_path, 
            mimetype=mime_type,
            as_attachment=True, 
            download_name=doc.original_name, 
        )
    except Exception as e:
        current_app.logger.exception(f"System Error in Download: {str(e)}")
        flash("Could not process your download request due to a server error.", "error")
        return redirect(safe_redirect_target(request.referrer, url_for("tasks.daily_tasks_list")))

@documents_bp.route("/view-document/<int:doc_id>/<filename>")
@login_required
def view_document(doc_id, filename):
    try:
        doc = CRMDocument.query.filter_by(
            id=doc_id, organization_id=current_user.organization_id
        ).first_or_404()
        
        upload_folder = current_app.config.get("UPLOAD_FOLDER")
        if not upload_folder:
            upload_folder = os.path.join(current_app.root_path, "static", "uploads")
            
        file_path = os.path.join(upload_folder, "crm_docs", doc.filename)
        file_path = os.path.normpath(file_path)
        
        import mimetypes
        mime_type, _ = mimetypes.guess_type(doc.original_name)
        ext = doc.original_name.lower().split('.')[-1] if '.' in doc.original_name else ''
        if ext == 'pdf':
            mime_type = 'application/pdf'
        elif ext in ['jpg', 'jpeg']:
            mime_type = 'image/jpeg'
        elif ext == 'png':
            mime_type = 'image/png'
            
        from flask import Response
        with open(file_path, "rb") as f:
            content = f.read()
            
        response = Response(content, mimetype=mime_type)
        response.headers["Content-Disposition"] = "inline"
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        # Security headers to allow embedding
        response.headers.pop("X-Frame-Options", None)
        return response
    except Exception as e:
        return str(e), 500


@documents_bp.route("/delete-document/<int:doc_id>", methods=["POST"])
@login_required
def delete_document(doc_id):
    doc = CRMDocument.query.filter_by(
        id=doc_id, organization_id=current_user.organization_id
    ).first_or_404()

    # Optional: Check permissions (e.g. uploader or admin)

    try:
        # Delete from filesystem
        file_path = os.path.join(
            current_app.config["UPLOAD_FOLDER"], "crm_docs", doc.filename
        )
        if os.path.exists(file_path):
            os.remove(file_path)

        db.session.delete(doc)
        db.session.commit()
        flash("Document deleted.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error: {str(e)}", exc_info=True)
        flash("An error occurred. Please try again.", "error")

    return redirect(safe_redirect_target(request.referrer, url_for("home_page")))
