from flask import Blueprint, render_template, request, flash, redirect, url_for, abort, jsonify
from flask_login import login_required, current_user
from model import UserRole, db
from utils.security import require_roles
from utils.trash import get_trashed_records, restore_record, permanently_delete_record, TRASH_MODELS

trash_bp = Blueprint("trash", __name__, url_prefix="/workplace/trash")

@trash_bp.route("/")
@login_required
@require_roles(UserRole.ADMIN, UserRole.MANAGER)
def trash_list():
    org_id = current_user.organization_id
    module = request.args.get("module", "customers")
    search = request.args.get("search", "")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 30, type=int)

    if module not in TRASH_MODELS:
        module = "customers"

    pagination = get_trashed_records(module, org_id, page, per_page, search)
    records = pagination.items if pagination else []

    return render_template(
        "trash/list.html",
        records=records,
        pagination=pagination,
        current_module=module,
        search=search,
        modules=list(TRASH_MODELS.keys())
    )

@trash_bp.route("/restore/<string:module>/<int:record_id>", methods=["POST"])
@login_required
@require_roles(UserRole.ADMIN, UserRole.MANAGER)
def restore_item(module, record_id):
    org_id = current_user.organization_id
    actor_id = current_user.employee.id if current_user.employee else None
    
    success, message = restore_record(module, record_id, org_id, actor_id)
    if success:
        flash(message, "success")
    else:
        flash(message, "error")
        
    return redirect(url_for('trash.trash_list', module=module))

@trash_bp.route("/delete/<string:module>/<int:record_id>", methods=["POST"])
@login_required
@require_roles(UserRole.ADMIN, UserRole.MANAGER)
def delete_item(module, record_id):
    org_id = current_user.organization_id
    actor_id = current_user.employee.id if current_user.employee else None
    
    success, message = permanently_delete_record(module, record_id, org_id, actor_id)
    if success:
        flash(message, "success")
    else:
        flash(message, "error")
        
    return redirect(url_for('trash.trash_list', module=module))

@trash_bp.route("/bulk-action", methods=["POST"])
@login_required
@require_roles(UserRole.ADMIN, UserRole.MANAGER)
def bulk_action():
    org_id = current_user.organization_id
    actor_id = current_user.employee.id if current_user.employee else None
    
    data = request.json
    action = data.get('action')
    module = data.get('module')
    record_ids = data.get('record_ids', [])
    
    if not action or not module or not record_ids:
        return jsonify({"success": False, "message": "Missing required data"}), 400
        
    success_count = 0
    for record_id in record_ids:
        if action == 'restore':
            success, _ = restore_record(module, record_id, org_id, actor_id)
        elif action == 'delete':
            success, _ = permanently_delete_record(module, record_id, org_id, actor_id)
        else:
            return jsonify({"success": False, "message": "Invalid action"}), 400
            
        if success:
            success_count += 1
            
    return jsonify({
        "success": True, 
        "message": f"Successfully processed {success_count} out of {len(record_ids)} records."
    })
