from flask import Blueprint, render_template, request, flash, redirect, url_for, abort, jsonify
from flask_login import login_required, current_user
from model import UserRole, db
from utils.security import require_roles
from utils.trash import get_trashed_records, restore_record, permanently_delete_record, TRASH_MODELS, TrashManager

trash_bp = Blueprint("trash", __name__, url_prefix="/workplace/trash")

@trash_bp.route("/")
@login_required
@require_roles(UserRole.ADMIN, UserRole.MANAGER)
def trash_list():
    org_id = current_user.organization_id
    module = request.args.get("module", "all")
    search = request.args.get("search", "")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 30, type=int)

    if module != "all" and module not in TRASH_MODELS:
        module = "all"

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
    action = data.get("action")
    record_ids = data.get("record_ids", [])
    current_module_context = data.get("module", "all")

    if action not in ["restore", "delete"] or not record_ids:
        return jsonify({"success": False, "message": "Invalid request"}), 400

    success_count = 0
    error_count = 0

    for item_data in record_ids:
        # If 'all' module, item_data should be a string like "customers:12"
        # If not, item_data might just be an int ID
        try:
            if isinstance(item_data, str) and ":" in item_data:
                mod_name, rec_id_str = item_data.split(":", 1)
                rec_id = int(rec_id_str)
            else:
                mod_name = current_module_context
                rec_id = int(item_data)
                
            if mod_name not in TRASH_MODELS:
                error_count += 1
                continue
                
            if action == "restore":
                success, msg = restore_record(mod_name, rec_id, org_id, actor_id)
            else:
                success, msg = permanently_delete_record(mod_name, rec_id, org_id, actor_id)
                
            if success:
                success_count += 1
            else:
                error_count += 1
        except Exception as e:
            error_count += 1
            
    return jsonify({
        "success": True, 
        "message": f"Successfully processed {success_count} out of {len(record_ids)} records."
    })

@trash_bp.route("/scan-dependencies", methods=["GET"])
@login_required
@require_roles(UserRole.ADMIN, UserRole.MANAGER)
def scan_dependencies():
    module = request.args.get("module")
    record_id = request.args.get("record_id", type=int)
    org_id = current_user.organization_id

    if not module or not record_id or module not in TRASH_MODELS:
        return jsonify({"success": False, "message": "Invalid request"}), 400

    name_display, deps = TrashManager.scan_dependencies(module, record_id, org_id)
    return jsonify({
        "success": True,
        "record_name": name_display,
        "module": module,
        "dependencies": deps,
        "has_dependencies": bool(deps),
    })
