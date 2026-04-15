import os
import uuid
from flask import Blueprint, request, flash, redirect, url_for, send_from_directory, current_app, abort
from flask_login import login_required, current_user
from model import db, CRMDocument, Lead, Project
from werkzeug.utils import secure_filename

documents_bp = Blueprint('documents', __name__)

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx', 'txt'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@documents_bp.route('/upload-document', methods=['POST'])
@login_required
def upload_document():
    entity_type = request.form.get('entity_type')
    entity_id = request.form.get('entity_id')
    description = request.form.get('description', '')

    if not entity_type or not entity_id:
        flash('Missing entity information.', 'error')
        return redirect(request.referrer)

    if 'file' not in request.files:
        flash('No file part.', 'error')
        return redirect(request.referrer)

    file = request.files['file']
    if file.filename == '':
        flash('No selected file.', 'error')
        return redirect(request.referrer)

    from utils.documents import handle_file_upload
    success = handle_file_upload(
        file=file,
        entity_type=entity_type,
        entity_id=entity_id,
        organization_id=current_user.organization_id,
        uploader_id=current_user.employee.id if current_user.employee else None,
        description=description
    )

    if success:
        db.session.commit()
        flash('Document uploaded successfully!', 'success')
    else:
        flash('File type not allowed or upload failed.', 'error')
        
    return redirect(request.referrer)

from flask import send_file

@documents_bp.route('/download-document/<int:doc_id>')
@login_required
def download_document(doc_id):
    try:
        # First, find the document across all organizations to diagnose potential mismatches
        doc = CRMDocument.query.get_or_404(doc_id)
        
        user_org = current_user.organization_id
        doc_org = doc.organization_id
        
        print(f"DEBUG: Download Request - ID: {doc_id}, File: {doc.filename}")
        print(f"DEBUG: User Org: {user_org}, Doc Org: {doc_org}")
        
        # Enforce organization isolation
        if user_org != doc_org:
            print(f"DEBUG: SECURITY ALERT - User {current_user.username} (Org {user_org}) tried accessing Doc {doc_id} (Org {doc_org})")
            flash("Permission denied: Document belongs to another organization.", "error")
            return redirect(request.referrer or url_for('tasks.daily_tasks_list'))
            
        # Build absolute path
        upload_folder = current_app.config.get('UPLOAD_FOLDER')
        if not upload_folder:
             upload_folder = os.path.join(current_app.root_path, 'static', 'uploads')
             
        file_path = os.path.join(upload_folder, 'crm_docs', doc.filename)
        file_path = os.path.normpath(file_path)
        
        print(f"DEBUG: Final File Path: {file_path}")
        
        if not os.path.exists(file_path):
            print(f"DEBUG: FILE MISSING ON DISK: {file_path}")
            flash("File not found on server storage.", "error")
            return redirect(request.referrer or url_for('tasks.daily_tasks_list'))
            
        return send_file(
            file_path,
            as_attachment=True,
            download_name=doc.original_name,
            max_age=0
        )
    except Exception as e:
        import traceback
        print(f"System Error in Download: {str(e)}")
        print(traceback.format_exc())
        flash("Could not process your download request due to a server error.", "error")
        return redirect(request.referrer or url_for('tasks.daily_tasks_list'))

@documents_bp.route('/delete-document/<int:doc_id>', methods=['POST'])
@login_required
def delete_document(doc_id):
    doc = CRMDocument.query.filter_by(id=doc_id, organization_id=current_user.organization_id).first_or_404()
    
    # Optional: Check permissions (e.g. uploader or admin)
    
    try:
        # Delete from filesystem
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'crm_docs', doc.filename)
        if os.path.exists(file_path):
            os.remove(file_path)
            
        db.session.delete(doc)
        db.session.commit()
        flash('Document deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting document: {str(e)}', 'error')
        
    return redirect(request.referrer)
