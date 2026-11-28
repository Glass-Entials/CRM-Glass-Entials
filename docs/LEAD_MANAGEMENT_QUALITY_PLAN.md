# GlassEntials CRM — Lead Management Engineering Quality & Test Plan

**Document Version:** 1.0  
**Module:** Lead Management (`leads_bp`) & Pre-Sales Integrations  
**Target Repository:** `Glass-Entials/CRM-Glass-Entials`  
**Status:** Active QA Baseline & Remediation Plan  
**Date:** November 2026  

---

## Table of Contents

1. [Purpose & Scope](#1-purpose--scope)
2. [System Under Test — Architecture & Inventory](#2-system-under-test--architecture--inventory)
3. [Pre-Testing Code Audit — Known Defects, Security & Integrity Gaps](#3-pre-testing-code-audit--known-defects-security--integrity-gaps)
4. [Engineering QA Test Matrix](#4-engineering-qa-test-matrix)
5. [Cross-Module Integration Verification](#5-cross-module-integration-verification)
6. [Security, Multi-Tenancy & RBAC Verification Checklist](#6-security-multi-tenancy--rbac-verification-checklist)
7. [Test Automation & Remediation Roadmap](#7-test-automation--remediation-roadmap)
8. [Quality Gate & Release Exit Criteria](#8-quality-gate--release-exit-criteria)

---

## 1. Purpose & Scope

This document defines the engineering Quality Assurance (QA) plan, test matrix, security verification boundaries, and defect remediation roadmap for the **Lead Management** module of GlassEntials CRM.

### In-Scope Components
* **Routes & Controllers:** `routes/leads.py` (`leads_bp`), `routes/documents.py` (`documents_bp` for lead attachments), `routes/contacts.py` (`convert_to_lead`), `routes/call_logger_api.py` (caller matching), `routes/quotations.py` (`api_create_lead` & quotation linking), `routes/orders.py` (`api_search_leads`, `api_get_lead_detail`, `api_create_lead_inline`, `confirm_lead_to_order`), and `routes/tasks.py` (`lead_id` task linkage).
* **Data Models (`model.py`):** `Lead`, `LeadStatus`, `LeadSource`, `LeadActivity`, `ActivityType`, `LeadComment`, `LeadSystemLog`, `LeadFollowUp`, `FollowUpMethod`, `FollowUpOutcome`, `CRMDocument`, `Task`, `Customer`, and `OrderMaster`.
* **Utilities & Trash:** `utils/lead_log.py` (`log_lead_event`), `utils/activity.py` (`log_activity`, `build_changes`), `utils/notifications.py` (`create_notification`), `utils/security.py` (`tenant_record_id`), `utils/exports.py`, and `utils/trash.py` (`DependencyRegistry` for `"leads"`).
* **Templates (`templates/leads/`):** `Lead.html`, `addLead.html`, `editlead.html`, `bulkuploadleads.html`, `follow_ups.html`, and `lead_profile.html`.

### Current Test Coverage Baseline
* As of this plan, `tests/` contains **13 automated unit/remediation suites (151 tests)** focused on OMS, Billing/PhonePe, CSRF, and Private Upload Storage (`scripts/run_all_remediation_tests.py`).
* **Gap:** There is **no dedicated automated unit test suite** covering `routes/leads.py` CRUD, CSV bulk upload, follow-ups, or `convert_lead`. Only the OMS Lead-to-Order APIs (`/orders/api/leads/*`) have automated tests in `tests/test_oms_master_order_creation.py`.

---

## 2. System Under Test — Architecture & Inventory

### 2.1 Route Inventory (`routes/leads.py`)

| Route | Methods | Handler | Primary Entities | Template / Response |
| :--- | :--- | :--- | :--- | :--- |
| `/leads` | `GET` | `leads_list` | `Lead`, `Employee` | `leads/Lead.html` |
| `/view-lead/<int:lead_id>` | `GET` | `view_lead` | `Lead`, `LeadFollowUp`, `Task`, `LeadActivity`, `LeadComment`, `LeadSystemLog` | `leads/lead_profile.html` |
| `/add-lead` | `GET`, `POST` | `add_lead` | `Lead`, `CallLog`, `Notification`, `LeadSystemLog`, `ActivityLog` | `leads/addLead.html` / redirect |
| `/edit-lead/<int:lead_id>` | `GET`, `POST` | `edit_lead` | `Lead`, `Notification`, `LeadSystemLog`, `ActivityLog` | `leads/editlead.html` / redirect |
| `/delete-lead/<int:lead_id>` | `POST` | `delete_lead` | `Lead` (soft delete), `ActivityLog` | Redirect `/leads` |
| `/export-leads/<string:format>` | `GET` | `export_leads` | `Lead` | CSV / XLSX / PDF download |
| `/convert-lead/<int:lead_id>` | `GET` | `convert_lead` | `Lead`, `Customer`, `Notification`, `LeadSystemLog`, `ActivityLog` | Redirect `/customers` or `/leads` |
| `/bulk-upload-leads` | `GET`, `POST` | `bulk_upload_leads` | `Lead` | `leads/bulkuploadleads.html` / redirect |
| `/add-activity/<int:lead_id>` | `POST` | `add_activity` | `LeadActivity`, `LeadSystemLog` | Redirect `/view-lead/<id>` |
| `/delete-lead-activity/<int:activity_id>` | `POST` | `delete_lead_activity` | `LeadActivity` | Redirect `/view-lead/<id>` |
| `/add-lead-comment/<int:lead_id>` | `POST` | `add_lead_comment` | `LeadComment`, `LeadSystemLog`, `ActivityLog` | Redirect `/view-lead/<id>#lead-comments-block` |
| `/delete-lead-comment/<int:comment_id>` | `POST` | `delete_lead_comment` | `LeadComment` | Redirect `/view-lead/<id>#lead-comments-block` |
| `/leads/follow-ups` | `GET` | `all_follow_ups` | `LeadFollowUp`, `Lead` | `leads/follow_ups.html` |
| `/lead/<int:lead_id>/follow-up/add` | `POST` | `add_follow_up` | `LeadFollowUp`, `Notification`, `LeadSystemLog`, `ActivityLog` | Redirect `/view-lead/<id>#followups` |
| `/lead/follow-up/<int:fu_id>/done` | `POST` | `mark_follow_up_done` | `LeadFollowUp`, `LeadSystemLog` | Redirect `/view-lead/<id>` |
| `/lead/follow-up/<int:fu_id>/delete` | `POST` | `delete_follow_up` | `LeadFollowUp` | Redirect `/view-lead/<id>` |
| `/lead/<int:lead_id>/add-task` | `POST` | `add_lead_task` | `Task`, `LeadSystemLog` | Redirect `/view-lead/<id>#tasks` |
| `/lead/task/<int:task_id>/complete` | `POST` | `complete_lead_task` | `Task`, `LeadSystemLog` | Redirect `/view-lead/<id>#tasks` |
| `/lead/task/<int:task_id>/delete` | `POST` | `delete_lead_task` | `Task` | Redirect `/view-lead/<id>#tasks` |

### 2.2 Database Constraints & Invariants (`model.py`)
* **Tenancy:** `Lead.organization_id` is `NOT NULL` indexed FK to `organization.id`.
* **Uniqueness:** Composite constraints `uq_lead_email_org` (`email`, `organization_id`) and `uq_lead_phone_org` (`phone_number`, `organization_id`).
* **Customer Parity Mismatch:** On `Lead`, `email` and `phone_number` are nullable (`nullable=True`, validated as "at least one required" in `add_lead`). On `Customer`, both `email` and `phone_number` are `nullable=False`.
* **Circular Reference Handling (`utils/trash.py`):** `Lead.contact_id -> Contact.id` and `Contact.lead_id -> Lead.id`. Permanent deletion in Trash nulls `lead.contact_id` before removing `Contact` / `Lead` rows.

---

## 3. Pre-Testing Code Audit — Known Defects, Security & Integrity Gaps

Static inspection of the codebase identified the following concrete issues that must be tracked, fixed, and regression-tested:

| ID | Severity | Location | Finding / Failure Mode | Recommended Fix |
| :--- | :--- | :--- | :--- | :--- |
| **DEF-01** | **P0 (Blocker)** | `routes/leads.py:300` | Uncommitted syntax error `db.session.flush()x` in `add_lead()` working tree. | Remove trailing `x` and add syntax/import smoke test. |
| **DEF-02** | **P1 (Crash)** | `routes/leads.py` (multiple lines: `125`, `154`, `287`, `442`, `506`, `626`, `689`, `796`, `914`, `966`, `1025`, `1045`) | Direct access to `current_user.employee.id` / `.name` without checking `if current_user.employee`. Any user account without an `Employee` profile triggers `AttributeError: 'NoneType'`. | Introduce `_actor_emp_id()` / `_actor_name()` helper or enforce employee presence at login/decorator level. |
| **DEF-03** | **P1 (Security / CSRF)** | `routes/leads.py:575` (`convert_lead`), `templates/leads/Lead.html:347`, `templates/leads/lead_profile.html:327` | `/convert-lead/<lead_id>` is a state-mutating HTTP `GET` route triggered via `<a href>`. Bypasses CSRF protection and is vulnerable to link-prefetch side effects. | Change `/convert-lead/<int:lead_id>` to `methods=["POST"]` and wrap UI triggers in CSRF-protected `<form method="POST">`. |
| **DEF-04** | **P1 (Data Integrity)** | `routes/leads.py:585-635` (`convert_lead`) | Converting a Lead that has only a phone number (`email=None`) or only an email (`phone_number=None`) fails with `500 IntegrityError` because `Customer.email` and `Customer.phone_number` are `nullable=False`. | Validate that both `email` and `phone_number` are populated before conversion and return an actionable validation flash message linking to Edit Lead. |
| **DEF-05** | **P1 (Data Integrity)** | `routes/leads.py:752-799` (`bulk_upload_leads`) | CSV importer does not strip `+91`/`91` prefixes, does not validate 10-digit phone format, and does not check soft-deleted (`is_deleted=True`) Trash records. Inserting a trashed phone/email triggers `IntegrityError` on `uq_lead_phone_org`/`uq_lead_email_org` and aborts the entire CSV batch. | Reuse a shared phone normalization & active+trash duplicate checker; report per-row skipped/error counts cleanly. |
| **DEF-06** | **P1 (Crash / Input)** | `routes/leads.py:49-51` (`leads_list`) & `820-832` (`all_follow_ups`) | `int(assigned_to)` raises unhandled `ValueError` (HTTP `500`) if `?assigned_to=abc` is passed in the URL query string. | Use `request.args.get("assigned_to", type=int)`. |
| **DEF-07** | **P1 (Logic Bug)** | `routes/leads.py:842-865` (`all_follow_ups`) | Query filters `?assigned_to=` and `?due=today` are applied only to `overdue_query`, not to `upcoming` or `completed` follow-ups. | Apply `assigned_to` filter consistently across `overdue`, `upcoming`, and `completed` queries. |
| **DEF-08** | **P1 (Tenant / State)** | `routes/leads.py` (`view_lead`, `edit_lead`, `convert_lead`, `add_activity`, `add_lead_comment`, `add_follow_up`, `add_lead_task`) | Queries filter `Lead.query.filter_by(id=lead_id, organization_id=...)` without `is_deleted=False`, allowing users with a direct URL to edit, convert, or schedule follow-ups on soft-deleted Leads in Trash. Additionally, `view_lead:68` omits `organization_id` on `LeadFollowUp.query.filter_by(lead_id=lead_id)`. | Add `is_deleted=False` to active lead routes (or block mutations with a Trash restore notice) and include `organization_id=current_user.organization_id` on `LeadFollowUp` queries. |
| **DEF-09** | **P1 (Storage Sync)** | `routes/documents.py:237-243` (`delete_document`) | Deletes files using `current_app.config["UPLOAD_FOLDER"]` instead of `_resolve_document_file_path(doc.filename)` (which supports `PRIVATE_UPLOAD_FOLDER` + legacy fallback). | Use `_resolve_document_file_path(doc.filename)` in `delete_document` so private storage files are actually unlinked and quota is reclaimed. |
| **DEF-10** | **P2 (Workflow)** | `routes/leads.py:635` vs `services/oms/order_master_service.py:623` | `convert_lead` sets `lead.status = LeadStatus.ACTIVE`, whereas OMS `create_order_from_confirmed_lead` expects `Closed` / `Confirmed` / `Order Confirmed`. | Document status semantics clearly and optionally allow `Order Confirmed` / `Closed` alignment on OMS handoff. |
| **DEF-11** | **P2 (Template Hygiene)** | `templates/leads/Lead.html:349`, `follow_ups.html`, `lead_profile.html` | `Lead.html:349` has escaped quotes `\'` inside `onclick` JS; `follow_ups.html` and `lead_profile.html` duplicate `<input type="hidden" name="csrf_token">` inside multiple forms. | Clean up escaped JS string quotes and remove duplicate CSRF hidden inputs. |

---

## 4. Engineering QA Test Matrix

### Suite A: Lead Creation, Normalization & Deduplication

| Test ID | Scenario | Preconditions / Input | Expected Result | Priority |
| :--- | :--- | :--- | :--- | :--- |
| **LM-01** | Create lead with valid 10-digit phone & email | Authenticated user; valid `name`, `9876543210`, `test@acme.com`. | Lead persisted with `organization_id`, `created_by`, `LeadStatus.NEW`; `LeadSystemLog` (`lead_created`) and `ActivityLog` (`create`) created; redirects to `/leads`. | P0 |
| **LM-02** | Phone normalization (`+91` / formatting) | Input phone `+91 98765-43210` or `919876543210`. | Stored `phone_number` normalized to `9876543210`. | P0 |
| **LM-03** | Invalid phone length rejected | Input `12345` or `98765432101` (11 digits). | Rejected with validation flash error `"Phone number must be exactly 10 digits."`; `form_data` preserved on `addLead.html`. | P1 |
| **LM-04** | Missing contact methods rejected | `name="Test"`, `phone=""`, `email=""`. | Rejected with `"Please provide at least a Phone Number or Email."`. | P1 |
| **LM-05** | Active duplicate detection (phone & email) | Existing active lead in Org 1 with same phone or email. | Creation blocked with specific duplicate message naming existing lead. | P0 |
| **LM-06** | Trash duplicate detection | Lead with same phone/email exists with `is_deleted=True` in Org 1. | Creation blocked with message instructing user to restore from Trash. | P1 |
| **LM-07** | Cross-org duplicate isolation | Org 2 already has lead with `9876543210`. Org 1 adds `9876543210`. | Allowed cleanly; Org 1 and Org 2 records remain strictly isolated. | P0 |

### Suite B: Lead Editing, Assignment & Audit Trail

| Test ID | Scenario | Preconditions / Input | Expected Result | Priority |
| :--- | :--- | :--- | :--- | :--- |
| **LM-08** | Reassign lead to colleague in same org | Update `assigned_to` from `None` or Emp A to Emp B (same org). | `lead.assigned_to` updated; `create_notification` sent to Emp B; `lead_assigned` `LeadSystemLog` created. | P0 |
| **LM-09** | Cross-tenant assignee tampering blocked | `POST /edit-lead/<id>` with `assigned_to=<emp_id_from_org2>`. | `tenant_record_id` rejects foreign ID (`assigned_to` becomes `None`); Org 2 employee receives no notification. | P0 |
| **LM-10** | Status change audit logging | Change `status` from `New` to `Prospect`. | `LeadSystemLog` (`status_changed`) records old & new status; `ActivityLog` records `build_changes` diff. | P1 |
| **LM-11** | Self-exclusion on duplicate check during edit | Edit lead without changing its own `phone_number` or `email`. | Update succeeds; `Lead.id != lead.id` prevents self-collision. | P0 |

### Suite C: Bulk CSV Upload & Exports

| Test ID | Scenario | Preconditions / Input | Expected Result | Priority |
| :--- | :--- | :--- | :--- | :--- |
| **LM-12** | Bulk CSV UTF-8 & CP1252/Latin-1 import | CSV with headers `Name,Email,Phone,Company,City,Source,Status`. | Valid unique rows inserted with `created_by` and `organization_id`. | P1 |
| **LM-13** | Missing required CSV headers | CSV missing `Phone` or `Email` column. | Rejected safely with `"Missing required columns."`. | P1 |
| **LM-14** | Bulk CSV skip duplicates & empty phones | CSV containing duplicate active phone/email and blank phone row. | Duplicate/empty rows skipped without failing valid rows. *(Verify DEF-05 fix for Trash & `+91` rows.)* | P1 |
| **LM-15** | Export CSV / Excel / PDF | Leads exist in Org 1 and Org 2. | `/export-leads/csv`, `/export-leads/excel`, `/export-leads/pdf` return HTTP `200` containing **only Org 1** active (`is_deleted=False`) leads. | P1 |
| **LM-16** | Invalid export format | `GET /export-leads/xml`. | Redirects with `"Invalid format."` flash error (no 500 error). | P2 |

### Suite D: Lead 360° Profile (Activities, Comments, Tasks, Documents)

| Test ID | Scenario | Preconditions / Input | Expected Result | Priority |
| :--- | :--- | :--- | :--- | :--- |
| **LM-17** | Log & delete manual activity | `POST /add-activity/<id>` (`Call`, description); then delete by creator vs non-creator employee vs manager. | Creator and `admin`/`manager` can delete; non-creator `employee` is rejected with permission flash. | P1 |
| **LM-18** | Add & delete team comment | `POST /add-lead-comment/<id>` with empty string vs valid text. | Empty comment rejected; valid comment creates `LeadComment`, `LeadSystemLog`, and `ActivityLog`. | P1 |
| **LM-19** | Lead-linked Task lifecycle | `POST /lead/<id>/add-task` -> `/complete` -> `/delete`. | Task created with `lead_id`, `organization_id`, `TaskStatus.PENDING`; completion sets `COMPLETED` and logs `task_completed` event. | P1 |
| **LM-20** | Upload, preview & download Lead document | `POST /upload-document` (`entity_type="lead"`, `entity_id=<id>`, PDF/PNG). | Stored in private upload folder (`crm_docs`), logged as `document_uploaded` in `LeadSystemLog`; download/view scoped by `organization_id`. | P0 |
| **LM-21** | Disallowed document extension blocked | Upload `.exe` or `.js` to `entity_type="lead"`. | Rejected cleanly; nothing persisted on disk or DB. | P0 |
| **LM-22** | Delete Lead document & reclaim storage quota | `POST /delete-document/<doc_id>`. | File removed from private storage, `subtract_storage_usage` updates org quota, `document_deleted` logged. *(Verify DEF-09 fix.)* | P1 |

### Suite E: Follow-Up Workflow

| Test ID | Scenario | Preconditions / Input | Expected Result | Priority |
| :--- | :--- | :--- | :--- | :--- |
| **LM-23** | Schedule follow-up & notify assignee | Lead assigned to Emp B; Emp A posts `/lead/<id>/follow-up/add` with `method=WhatsApp`, `follow_up_date`. | `LeadFollowUp` saved (`is_done=False`); Emp B receives notification; `follow_up_scheduled` logged. | P0 |
| **LM-24** | Complete follow-up with outcome & notes | `POST /lead/follow-up/<fu_id>/done` (`outcome=Interested`, notes). | `is_done=True`, `outcome` updated, `follow_up_done` logged. | P1 |
| **LM-25** | Global follow-up buckets (Overdue / Upcoming / Completed) | Seed past-due, future, and done follow-ups across active and soft-deleted leads. | `GET /leads/follow-ups` places items in correct buckets and excludes follow-ups belonging to `is_deleted=True` leads. | P1 |
| **LM-26** | Filter follow-ups by `assigned_to` and `due=today` | `GET /leads/follow-ups?assigned_to=<emp_id>&due=today`. | Filtered correctly without HTTP 500 on invalid `assigned_to`. *(Verify DEF-06 & DEF-07 fixes.)* | P1 |
| **LM-27** | `lead.next_follow_up` property | Lead has 1 past-due incomplete, 2 future incomplete, and 1 future completed follow-up. | Returns the earliest future incomplete follow-up. | P2 |

### Suite F: Cross-Module Conversion Workflows

| Test ID | Scenario | Preconditions / Input | Expected Result | Priority |
| :--- | :--- | :--- | :--- | :--- |
| **LM-28** | Convert Lead to Customer (happy path) | Lead has both valid `phone_number` and `email`, not yet converted. | `Customer` created with `lead_id=lead.id` and copied GST/address/notes; `lead.converted_customer` populated; assignee notified; `lead_converted` logged. | P0 |
| **LM-29** | Prevent duplicate conversion or duplicate customer | Lead already converted OR active/trashed `Customer` exists with matching email/phone. | Blocked cleanly; if customer is in Trash, shows restore link. | P0 |
| **LM-30** | Contact-to-Lead conversion | `POST` to `contacts.convert_to_lead` for an active Contact. | Creates `Lead` with `contact_id`, sets `contact.status=ContactStatus.LEAD` and `contact.lead_id`. | P1 |
| **LM-31** | Call Logger auto-match & manual link | Incoming CallLog via `call_logger_api.py` matching lead phone; OR `/add-lead?call_id=<id>`. | CallLog automatically attaches `lead_id`; manual creation with `?call_id` back-links `call.lead_id` and `caller_name_snapshot`. | P1 |
| **LM-32** | Lead-to-OMS Order integration & idempotency | `GET /orders/api/leads/<id>` (converted vs unconverted) and `POST /orders/api/leads/<id>/confirm-to-order`. | Detects existing converted/matching customer (`detect_lead_customer`); `confirm-to-order` creates `OrderMaster` once and returns existing order on repeat calls. | P0 |

### Suite G: Security, Multi-Tenancy & Trash Lifecycle

| Test ID | Scenario | Preconditions / Input | Expected Result | Priority |
| :--- | :--- | :--- | :--- | :--- |
| **LM-33** | Cross-tenant IDOR isolation | Org 2 user accesses `/view-lead/<org1_id>`, `/edit-lead/<org1_id>`, `/delete-lead/<org1_id>`, `/convert-lead/<org1_id>`, `/lead/follow-up/<org1_fu_id>/done`, `/download-document/<org1_doc_id>`. | All endpoints return `404` (never leak existence or mutate Org 1 records). | P0 |
| **LM-34** | CSRF enforcement on all state changes | `POST` without `csrf_token` (with `WTF_CSRF_ENABLED=True`). | Rejected with HTTP `400` across all mutating endpoints (including `convert_lead` after DEF-03 fix). | P0 |
| **LM-35** | Soft-delete & Trash dependency cascade | Delete lead via `/delete-lead/<id>`, verify hidden in `/leads`, then restore and permanently purge from Trash. | Soft-delete hides lead from `/leads` and `/leads/follow-ups`; restore recovers it; permanent purge deletes child `LeadActivity`, `LeadComment`, `LeadSystemLog`, `LeadFollowUp`, `CRMDocument`, `Task` without FK errors (`utils/trash.py`). | P1 |
| **LM-36** | Unauthenticated access blocked | Anonymous requests to all 20 `leads_bp` routes. | Redirected to login (`302`) or `401`. | P0 |

---

## 5. Cross-Module Integration Verification

```
Contact ──(convert_to_lead)──► Lead ──(convert_lead)────────► Customer
  ▲                              │                                │
  │                              ├──(api/create-lead)──► Quotation│
  └──(CallLogger _find_lead)─────┼──(add-lead?call_id)            │
                                 └──(confirm-to-order)──► OrderMaster (OMS)
```

Key integration invariants to verify during regression cycles:
1. **Inline Lead Creation Parity:** Both `routes/orders.py` (`/orders/api/leads/inline`) and `routes/quotations.py` (`/api/create-lead`) create `Lead` records directly. Ensure duplicate rules and `created_by` handling behave consistently with `routes/leads.py`.
2. **Trash Safety Across Modules:** Any `Lead` referenced by an active `OrderMaster` (`OrderMaster.lead_id` with `ondelete="RESTRICT"`) cannot be permanently purged in `utils/trash.py` without either blocking or handling the FK constraint safely.
3. **OMS Stakeholder Notifications:** When an order is created from a Lead (`OrderMasterService.create_order`), notifications are strictly scoped to directly involved employees (`created_by_id`, `assigned_to_id`, active fitting/procurement/delivery owners) in `services/oms/notification_service.py`.

---

## 6. Security, Multi-Tenancy & RBAC Verification Checklist

* [ ] Every query on `Lead`, `LeadActivity`, `LeadComment`, `LeadSystemLog`, `LeadFollowUp`, `Task`, and `CRMDocument` scopes by `organization_id == current_user.organization_id`.
* [ ] All dropdown/foreign ID inputs (`assigned_to`, `lead_id`, `call_id`) pass through `tenant_record_id(...)` or explicit org filters.
* [ ] No state-changing action uses HTTP `GET` (remediate `convert_lead` per DEF-03).
* [ ] `CRMDocument` files attached to leads are stored outside `static/` when `PRIVATE_UPLOAD_FOLDER` is configured, and served strictly via `/download-document/<id>` and `/view-document/<id>/<filename>` with `Cache-Control: no-store`.
* [ ] Soft-deleted leads (`is_deleted=True`) cannot be modified or converted until restored from Trash (DEF-08).

---

## 7. Test Automation & Remediation Roadmap

### Phase 1 — Critical Fixes (Before Production Release of Lead Changes)
1. Fix syntax error in `routes/leads.py:300` (`DEF-01`).
2. Change `/convert-lead/<lead_id>` to `POST` with CSRF form buttons in `Lead.html` and `lead_profile.html` (`DEF-03`).
3. Add safe employee lookup helper for `current_user.employee` (`DEF-02`) and require non-empty `email` + `phone_number` before Customer conversion (`DEF-04`).
4. Fix `request.args.get("assigned_to", type=int)` (`DEF-06`) and guard against `is_deleted=True` lead mutations (`DEF-08`).

### Phase 2 — Consistency & Storage Fixes
1. Align `bulk_upload_leads` phone normalization and Trash duplicate handling with `add_lead` (`DEF-05`).
2. Apply `assigned_to` / `due=today` filters uniformly in `all_follow_ups` (`DEF-07`).
3. Update `delete_document` in `routes/documents.py` to use `_resolve_document_file_path` (`DEF-09`).
4. Clean up template quote escaping and duplicate CSRF inputs (`DEF-11`).

### Phase 3 — Automated Test Suite (`tests/test_lead_management.py`)
1. Create `tests/test_lead_management.py` covering test cases `LM-01` through `LM-36` using an isolated Flask + SQLite in-memory harness (matching `tests/test_oms_master_order_creation.py`).
2. Register `"Lead Management Workflow & Security"` in `scripts/run_all_remediation_tests.py` so every CI/local verification run executes Lead tests alongside the existing 151 tests.

---

## 8. Quality Gate & Release Exit Criteria

A Lead Management release or patch is considered **QA Approved** only when all conditions below are met:

1. **Zero P0 / P1 Open Defects:** All P0 and P1 items (`DEF-01` through `DEF-09`) are resolved or have approved mitigations.
2. **100% Automated Pass Rate:** `tests/test_lead_management.py` (`LM-01`–`LM-36`) and the full `scripts/run_all_remediation_tests.py` suite pass at **100%** with zero skipped security or tenant-isolation assertions.
3. **Cross-Module Smoke Verification:** Confirmed passing end-to-end flow: `Contact -> Lead -> Follow-Up -> Document Upload -> Customer Conversion -> OMS Master Order`.
4. **Security Sign-off:** All state-mutating routes enforce `POST` + CSRF validation and return `404` on cross-tenant access attempts.
