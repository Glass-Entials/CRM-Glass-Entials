import unittest
import os
import shutil
import tempfile
from io import BytesIO
from werkzeug.datastructures import FileStorage
from flask import Flask
from app import app
from model import db, CRMDocument, User, Organization, Employee
from services.oms.document_service import OMSDocumentService, OMSTenantIsolationError, OMSInvariantError


class SecureDocumentStorageTests(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.temp_dir = tempfile.mkdtemp()
        self.private_dir = os.path.join(self.temp_dir, "private_uploads")
        self.legacy_dir = os.path.join(self.temp_dir, "legacy_uploads")
        os.makedirs(self.private_dir, exist_ok=True)
        os.makedirs(self.legacy_dir, exist_ok=True)

        self.app.config["PRIVATE_UPLOAD_FOLDER"] = self.private_dir
        self.app.config["UPLOAD_FOLDER"] = self.private_dir
        self.app.config["LEGACY_UPLOAD_FOLDER"] = self.legacy_dir

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_oms_upload_dir_is_outside_static(self):
        """OMSDocumentService.get_upload_dir must reside inside PRIVATE_UPLOAD_FOLDER."""
        with self.app.app_context():
            upload_dir = OMSDocumentService.get_upload_dir(org_id=1)
            self.assertTrue(upload_dir.startswith(os.path.abspath(self.private_dir)))
            self.assertFalse("static" in upload_dir)

    def test_legacy_file_fallback_resolution(self):
        """If file exists only in legacy directory, it must still resolve successfully."""
        with self.app.app_context():
            # Create a mock legacy document
            org_id = 1
            order_id = 99
            doc_id = 888
            filename = "legacy_test_order_spec.pdf"
            legacy_org_dir = os.path.join(self.legacy_dir, "order_docs", f"org_{org_id}")
            os.makedirs(legacy_org_dir, exist_ok=True)
            legacy_file = os.path.join(legacy_org_dir, filename)
            with open(legacy_file, "wb") as f:
                f.write(b"%PDF-1.4 mock content")

            # Mock OrderDocument record
            from unittest.mock import MagicMock, patch
            mock_doc = MagicMock()
            mock_doc.id = doc_id
            mock_doc.order_id = order_id
            mock_doc.organization_id = org_id
            mock_doc.file_path = os.path.join(self.private_dir, "order_docs", f"org_{org_id}", filename)
            mock_doc.is_deleted = False

            with patch("services.oms.document_service.OrderDocument") as mock_model:
                mock_model.query.filter_by.return_value.first.return_value = mock_doc
                doc, path = OMSDocumentService.get_document_for_download(org_id=org_id, order_id=order_id, doc_id=doc_id)
                self.assertEqual(os.path.realpath(path), os.path.realpath(legacy_file))

    def test_path_traversal_attack_blocked(self):
        """Any attempt to point file_path outside allowed directories must raise OMSInvariantError."""
        with self.app.app_context():
            from unittest.mock import MagicMock, patch
            mock_doc = MagicMock()
            mock_doc.id = 1
            mock_doc.order_id = 1
            mock_doc.organization_id = 1
            mock_doc.file_path = "C:/Windows/System32/drivers/etc/hosts" if os.name == "nt" else "/etc/passwd"
            mock_doc.is_deleted = False

            with patch("services.oms.document_service.OrderDocument") as mock_model:
                mock_model.query.filter_by.return_value.first.return_value = mock_doc
                with self.assertRaises(OMSInvariantError):
                    OMSDocumentService.get_document_for_download(org_id=1, order_id=1, doc_id=1)


if __name__ == "__main__":
    unittest.main()
