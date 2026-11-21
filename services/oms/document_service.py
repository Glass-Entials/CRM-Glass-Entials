import os
import uuid
import mimetypes
from typing import Optional, Dict, Any, Tuple
from flask import current_app
from werkzeug.utils import secure_filename
from werkzeug.datastructures import FileStorage

from model import db, OrderMaster, OrderDocument, OrderDocumentType, Employee
from services.oms.order_master_service import OMSInvariantError, OMSTenantIsolationError


ALLOWED_EXTENSIONS = {
    "pdf", "png", "jpg", "jpeg", "webp", "doc", "docx", "xls", "xlsx", "csv"
}

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
    "text/plain",
}

MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024  # 15 MB
ORG_STORAGE_QUOTA_BYTES = 500 * 1024 * 1024  # 500 MB per org OMS quota


class OMSDocumentService:
    """Enterprise document management for OMS with tenant isolation and strict validation."""

    @classmethod
    def get_upload_dir(cls, org_id: int) -> str:
        upload_root = (
            current_app.config.get("UPLOAD_FOLDER")
            or os.path.join(current_app.root_path, "uploads")
        )
        target_dir = os.path.abspath(
            os.path.join(upload_root, "order_docs", f"org_{org_id}")
        )
        os.makedirs(target_dir, exist_ok=True)
        return target_dir

    @classmethod
    def get_organization_storage_used(cls, org_id: int) -> int:
        """Calculate total storage consumed by OMS documents for the organization."""
        result = (
            db.session.query(db.func.coalesce(db.func.sum(OrderDocument.file_size), 0))
            .filter(
                OrderDocument.organization_id == org_id,
                OrderDocument.is_deleted == False,
            )
            .scalar()
        )
        return int(result or 0)

    @classmethod
    def save_order_document(
        cls,
        org_id: int,
        order_id: int,
        file: FileStorage,
        document_type: str,
        title: Optional[str] = None,
        is_customer_visible: bool = False,
        uploaded_by_id: int = 1,
    ) -> OrderDocument:
        """
        Validate, securely store, and index an order document.
        Ensures strict tenant isolation, quota management, and MIME verification.
        """
        # 1. Tenant & Order validation
        order = OrderMaster.query.filter_by(id=order_id, organization_id=org_id).first()
        if not order:
            raise OMSTenantIsolationError(f"Order #{order_id} not found in organization #{org_id}.")

        # 2. Document type enum check
        doc_type_upper = document_type.upper() if document_type else OrderDocumentType.OTHER.value
        valid_types = {t.value for t in OrderDocumentType}
        if doc_type_upper not in valid_types:
            raise OMSInvariantError(f"Invalid document type '{document_type}'. Valid: {sorted(valid_types)}")

        # 3. File presence & filename sanitization
        if not file or not file.filename:
            raise OMSInvariantError("No file provided for upload.")

        raw_filename = secure_filename(file.filename)
        if not raw_filename or "." not in raw_filename:
            raise OMSInvariantError("Invalid or missing file extension.")

        ext = raw_filename.rsplit(".", 1)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise OMSInvariantError(
                f"File extension '.{ext}' is not permitted. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )

        # 4. MIME type check
        reported_mime = file.mimetype or mimetypes.guess_type(raw_filename)[0] or "application/octet-stream"
        if reported_mime not in ALLOWED_MIME_TYPES and reported_mime != "application/octet-stream":
            raise OMSInvariantError(f"MIME type '{reported_mime}' is not supported.")

        # 5. Read file contents and verify size
        content = file.read()
        file_size = len(content)
        if file_size == 0:
            raise OMSInvariantError("Uploaded file is empty (0 bytes).")
        if file_size > MAX_FILE_SIZE_BYTES:
            raise OMSInvariantError(
                f"File size ({file_size / (1024*1024):.1f} MB) exceeds maximum limit of 15 MB."
            )

        # 6. Check storage quota
        storage_used = cls.get_organization_storage_used(org_id)
        if storage_used + file_size > ORG_STORAGE_QUOTA_BYTES:
            raise OMSInvariantError(
                f"Organization storage quota exceeded (used {storage_used / (1024*1024):.1f} MB of 500 MB limit)."
            )

        # 7. Safe physical storage (prevent path traversal)
        upload_dir = cls.get_upload_dir(org_id)
        unique_prefix = uuid.uuid4().hex[:12]
        safe_disk_filename = f"{unique_prefix}_{raw_filename}"
        target_path = os.path.abspath(os.path.join(upload_dir, safe_disk_filename))

        # Guarantee stored file stays inside upload_dir
        if not target_path.startswith(upload_dir):
            raise OMSInvariantError("Invalid file destination path (path traversal attempt).")

        with open(target_path, "wb") as f:
            f.write(content)

        # 8. Record in database
        doc = OrderDocument(
            order_id=order_id,
            organization_id=org_id,
            document_type=doc_type_upper,
            title=title.strip() if title else raw_filename,
            file_path=target_path,
            file_size=file_size,
            mime_type=reported_mime,
            is_customer_visible=is_customer_visible,
            uploaded_by_id=uploaded_by_id,
        )
        db.session.add(doc)
        db.session.commit()
        return doc

    @classmethod
    def get_document_for_download(
        cls,
        org_id: int,
        order_id: int,
        doc_id: int,
    ) -> Tuple[OrderDocument, str]:
        """
        Verify tenant ownership and physical file existence for download or preview.
        """
        doc = OrderDocument.query.filter_by(
            id=doc_id,
            order_id=order_id,
            organization_id=org_id,
            is_deleted=False,
        ).first()

        if not doc:
            raise OMSTenantIsolationError(
                f"Document #{doc_id} not found for Order #{order_id} in organization #{org_id}."
            )

        if not os.path.exists(doc.file_path):
            raise OMSInvariantError("Document file missing from server storage.")

        return doc, doc.file_path

    @classmethod
    def soft_delete_document(
        cls,
        org_id: int,
        order_id: int,
        doc_id: int,
    ) -> bool:
        """Soft delete a document record."""
        doc = OrderDocument.query.filter_by(
            id=doc_id,
            order_id=order_id,
            organization_id=org_id,
            is_deleted=False,
        ).first()

        if not doc:
            raise OMSTenantIsolationError(f"Document #{doc_id} not found.")

        doc.is_deleted = True
        db.session.commit()
        return True

    @classmethod
    def list_order_documents(cls, org_id: int, order_id: int) -> list:
        """List active documents for an order."""
        return (
            OrderDocument.query.filter_by(
                order_id=order_id,
                organization_id=org_id,
                is_deleted=False,
            )
            .order_by(OrderDocument.created_at.desc())
            .all()
        )
