import os
import uuid
from werkzeug.utils import secure_filename
from flask import current_app
from model import db, CRMDocument

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx', 'txt'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def handle_file_upload(file, entity_type, entity_id, organization_id, uploader_id, description=''):
    if file and allowed_file(file.filename):
        original_name = secure_filename(file.filename)
        extension = original_name.rsplit('.', 1)[1].lower()
        unique_filename = f"{uuid.uuid4().hex}.{extension}"
        
        upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'crm_docs')
        if not os.path.exists(upload_path):
            os.makedirs(upload_path)
        
        file.save(os.path.join(upload_path, unique_filename))

        new_doc = CRMDocument(
            filename=unique_filename,
            original_name=original_name,
            file_type=extension,
            file_size=0,
            description=description,
            uploaded_by=uploader_id,
            organization_id=organization_id
        )

        if entity_type == 'lead':
            new_doc.lead_id = entity_id
        elif entity_type == 'project':
            new_doc.project_id = entity_id
        elif entity_type == 'task':
            new_doc.task_id = entity_id
        elif entity_type == 'daily_task':
            new_doc.daily_task_id = entity_id
        
        db.session.add(new_doc)
        return True
    return False
