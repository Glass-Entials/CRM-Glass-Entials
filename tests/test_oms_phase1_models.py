import unittest
import sys
from pathlib import Path
from decimal import Decimal
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from model import (
    db,
    User,
    Organization,
    Customer,
    Vendor,
    Product,
    Quotation,
    Invoice,
    Payment,
    Employee,
    OrderMaster,
    OrderItem,
    OrderVendorProcurement,
    OrderItemVendorAllocation,
    OrderProcurementItem,
    OrderConsignment,
    ConsignmentItem,
    OrderDelivery,
    DeliveryItem,
    OrderFittingVisit,
    FittingVisitItem,
    OrderPaymentAllocation,
    VendorBill,
    VendorBillItem,
    VendorPayment,
    OrderDocument,
    OrderRevision,
    OrderStatusHistory,
    OrderExceptionalClosure,
    MasterOrderStatus,
    OrderProcurementStatus,
    OrderDeliveryStatus,
    OrderFittingStatus,
    OrderCustomerPaymentStatus,
    OrderVendorPaymentStatus,
    OrderItemAllocationStatus,
    OrderConsignmentStatus,
    OrderFittingVisitStatus,
    OrderPaymentAllocationType,
    VendorBillStatus,
    OrderDocumentType,
    OrderClosureType,
    ImmutableModelError,
)


class OMSPhase1ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask("oms_phase1_test")
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(cls.app)

    def setUp(self):
        self.context = self.app.app_context()
        self.context.push()
        # Enable foreign key enforcement in SQLite
        db.session.execute(text("PRAGMA foreign_keys = ON;"))
        db.create_all()

        # Seed prerequisite base records
        self.user1 = User(id=1, username="admin1", password="hash_password", email="admin1@example.com")
        self.user2 = User(id=2, username="tech1", password="hash_password", email="tech1@example.com")
        self.org1 = Organization(id=1, name="GlassEntials Demo Org", unique_code="ORG001")
        self.org2 = Organization(id=2, name="Competitor Org", unique_code="ORG002")
        db.session.add_all([self.user1, self.user2, self.org1, self.org2])
        db.session.commit()

        self.emp1 = Employee(id=1, user_id=1, name="Alice Admin", email="alice@test.com", organization_id=1)
        self.emp2 = Employee(id=2, user_id=2, name="Bob Tech", email="bob@test.com", organization_id=1)
        self.cust1 = Customer(id=1, name="Acme Construction", email="acme@test.com", phone_number="9876543210", organization_id=1, created_by=1)
        self.cust2 = Customer(id=2, name="Beta Infra", email="beta@test.com", phone_number="9876543211", organization_id=1, created_by=1)
        self.vend1 = Vendor(id=1, company_name="Saint-Gobain Glass", code="VEN-001", organization_id=1)
        self.vend2 = Vendor(id=2, company_name="Asahi India Glass", code="VEN-002", organization_id=1)
        self.prod1 = Product(id=1, name="12mm Toughened Glass", organization_id=1)

        db.session.add_all([
            self.emp1, self.emp2, self.cust1, self.cust2,
            self.vend1, self.vend2, self.prod1
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        try:
            db.session.execute(text("PRAGMA foreign_keys = OFF;"))
            db.drop_all()
        except Exception:
            pass
        self.context.pop()

    def _create_master_order(self, order_id=1, order_num="ORD-2026-0001", org_id=1):
        order = OrderMaster(
            id=order_id,
            master_order_number=order_num,
            organization_id=org_id,
            customer_id=1,
            created_by_id=1,
            total_ordered_amount=Decimal("150000.00"),
            total_tax_amount=Decimal("27000.00"),
            net_order_amount=Decimal("177000.00"),
        )
        db.session.add(order)
        db.session.commit()
        return order

    def test_01_order_master_and_item_creation_with_decimal_precision(self):
        """Test master order and item creation ensuring exact decimal precision on money & qty."""
        order = self._create_master_order()
        self.assertEqual(order.version_id, 1)
        self.assertEqual(order.status, MasterOrderStatus.DRAFT.value)
        self.assertEqual(order.net_order_amount, Decimal("177000.00"))

        item = OrderItem(
            id=1,
            order_id=order.id,
            organization_id=1,
            product_id=1,
            item_description="12mm Clear Toughened Glass 2400x1200",
            quantity=Decimal("100.125"),
            unit_price=Decimal("1500.50"),
            tax_rate=Decimal("18.00"),
            tax_amount=Decimal("27022.50"),
            discount_amount=Decimal("0.00"),
            total_price=Decimal("177260.56"),
        )
        db.session.add(item)
        db.session.commit()

        refetched = db.session.get(OrderItem, 1)
        self.assertEqual(refetched.quantity, Decimal("100.125"))
        self.assertEqual(refetched.unit_price, Decimal("1500.50"))
        self.assertEqual(refetched.total_price, Decimal("177260.56"))
        self.assertEqual(len(order.items), 1)
        self.assertEqual(order.items[0].id, 1)

    def test_02_multi_vendor_allocation_and_po_linkage(self):
        """Test splitting an OrderItem across multiple vendors and issuing a PO."""
        order = self._create_master_order()
        item = OrderItem(
            id=1,
            order_id=order.id,
            organization_id=1,
            item_description="Glass panels",
            quantity=Decimal("100.000"),
            unit_price=Decimal("1000.00"),
            total_price=Decimal("118000.00"),
        )
        db.session.add(item)
        db.session.commit()

        # Allocate 60 to Vendor 1 and 40 to Vendor 2
        alloc1 = OrderItemVendorAllocation(
            id=1,
            order_id=order.id,
            order_item_id=item.id,
            vendor_id=1,
            organization_id=1,
            allocated_quantity=Decimal("60.000"),
            expected_rate=Decimal("800.00"),
            estimated_cost=Decimal("48000.00"),
            created_by_id=1,
        )
        alloc2 = OrderItemVendorAllocation(
            id=2,
            order_id=order.id,
            order_item_id=item.id,
            vendor_id=2,
            organization_id=1,
            allocated_quantity=Decimal("40.000"),
            expected_rate=Decimal("850.00"),
            estimated_cost=Decimal("34000.00"),
            created_by_id=1,
        )
        db.session.add_all([alloc1, alloc2])
        db.session.commit()

        self.assertEqual(len(item.allocations), 2)

        # Issue Purchase Order to Vendor 1
        po = OrderVendorProcurement(
            id=1,
            po_number="PO-2026-0001",
            order_id=order.id,
            vendor_id=1,
            organization_id=1,
            created_by_id=1,
            total_procurement_amount=Decimal("48000.00"),
            net_procurement_amount=Decimal("56640.00"),
        )
        db.session.add(po)
        db.session.commit()

        po_item = OrderProcurementItem(
            id=1,
            procurement_id=po.id,
            allocation_id=alloc1.id,
            vendor_id=1,
            order_id=order.id,
            organization_id=1,
            quantity=Decimal("60.000"),
            unit_rate=Decimal("800.00"),
            total_rate=Decimal("48000.00"),
            line_total=Decimal("56640.00"),
        )
        db.session.add(po_item)
        db.session.commit()

        self.assertEqual(len(po.items), 1)
        self.assertEqual(po.items[0].allocation_id, alloc1.id)

    def test_03_cross_order_foreign_key_rejection(self):
        """Verify DB engine rejects linking a procurement item to a mismatched order_id."""
        order1 = self._create_master_order(order_id=1, order_num="ORD-001")
        order2 = self._create_master_order(order_id=2, order_num="ORD-002")

        item1 = OrderItem(
            id=1, order_id=1, organization_id=1,
            item_description="Panel", quantity=Decimal("10.000"),
            unit_price=Decimal("100.00"), total_price=Decimal("118.00")
        )
        alloc1 = OrderItemVendorAllocation(
            id=1, order_id=1, order_item_id=1, vendor_id=1, organization_id=1,
            allocated_quantity=Decimal("10.000"), expected_rate=Decimal("80.00"),
            estimated_cost=Decimal("800.00"), created_by_id=1
        )
        po1 = OrderVendorProcurement(
            id=1, po_number="PO-001", order_id=1, vendor_id=1, organization_id=1,
            created_by_id=1
        )
        db.session.add_all([item1, alloc1, po1])
        db.session.commit()

        # Attempt to insert OrderProcurementItem with order_id=2 (mismatched)
        with self.assertRaises(IntegrityError):
            mismatched_item = OrderProcurementItem(
                id=1,
                procurement_id=1, # belongs to order 1
                allocation_id=1,  # belongs to order 1
                vendor_id=1,
                order_id=2,       # Mismatched order_id!
                organization_id=1,
                quantity=Decimal("10.000"),
                unit_rate=Decimal("80.00"),
                total_rate=Decimal("800.00"),
                line_total=Decimal("944.00"),
            )
            db.session.add(mismatched_item)
            db.session.commit()
        db.session.rollback()

    def test_04_cross_vendor_allocation_rejection(self):
        """Verify PO for Vendor 1 cannot reference an allocation for Vendor 2."""
        order = self._create_master_order()
        item = OrderItem(
            id=1, order_id=1, organization_id=1,
            item_description="Panel", quantity=Decimal("10.000"),
            unit_price=Decimal("100.00"), total_price=Decimal("118.00")
        )
        alloc_vend2 = OrderItemVendorAllocation(
            id=1, order_id=1, order_item_id=1, vendor_id=2, organization_id=1,
            allocated_quantity=Decimal("10.000"), expected_rate=Decimal("80.00"),
            estimated_cost=Decimal("800.00"), created_by_id=1
        )
        po_vend1 = OrderVendorProcurement(
            id=1, po_number="PO-001", order_id=1, vendor_id=1, organization_id=1,
            created_by_id=1
        )
        db.session.add_all([item, alloc_vend2, po_vend1])
        db.session.commit()

        # PO 1 is issued to Vendor 1, but alloc 1 is for Vendor 2
        with self.assertRaises(IntegrityError):
            cross_vendor_item = OrderProcurementItem(
                id=1,
                procurement_id=1, # Vendor 1
                allocation_id=1,  # Vendor 2!
                vendor_id=1,
                order_id=1,
                organization_id=1,
                quantity=Decimal("10.000"),
                unit_rate=Decimal("80.00"),
                total_rate=Decimal("800.00"),
                line_total=Decimal("944.00"),
            )
            db.session.add(cross_vendor_item)
            db.session.commit()
        db.session.rollback()

    def test_05_immutability_of_audit_records(self):
        """Verify OrderStatusHistory, OrderRevision, and OrderExceptionalClosure reject update and delete."""
        order = self._create_master_order()

        # 1. OrderStatusHistory
        hist = OrderStatusHistory(
            id=1,
            order_id=order.id,
            organization_id=1,
            from_status="DRAFT",
            to_status="CONFIRMED",
            changed_by_id=1,
            remarks="Client approved PO.",
        )
        db.session.add(hist)
        db.session.commit()

        with self.assertRaises(ImmutableModelError):
            hist.remarks = "Tampered remark"
            db.session.commit()
        db.session.rollback()

        with self.assertRaises(ImmutableModelError):
            hist_to_del = db.session.get(OrderStatusHistory, 1)
            db.session.delete(hist_to_del)
            db.session.commit()
        db.session.rollback()

        # 2. OrderRevision
        rev = OrderRevision(
            id=1,
            order_id=order.id,
            organization_id=1,
            revision_number=1,
            snapshot_json='{"status": "CONFIRMED"}',
            change_reason="Baseline revision",
            created_by_id=1,
        )
        db.session.add(rev)
        db.session.commit()

        with self.assertRaises(ImmutableModelError):
            rev.change_reason = "Modified reason"
            db.session.commit()
        db.session.rollback()

        with self.assertRaises(ImmutableModelError):
            rev_to_del = db.session.get(OrderRevision, 1)
            db.session.delete(rev_to_del)
            db.session.commit()
        db.session.rollback()

        # 3. OrderExceptionalClosure
        closure = OrderExceptionalClosure(
            id=1,
            order_id=order.id,
            organization_id=1,
            closure_type=OrderClosureType.CANCELLED.value,
            closure_reason="Customer site insolvent.",
            approved_by_id=1,
        )
        db.session.add(closure)
        db.session.commit()

        with self.assertRaises(ImmutableModelError):
            closure.closure_reason = "Changed reason"
            db.session.commit()
        db.session.rollback()

        with self.assertRaises(ImmutableModelError):
            closure_to_del = db.session.get(OrderExceptionalClosure, 1)
            db.session.delete(closure_to_del)
            db.session.commit()
        db.session.rollback()

    def test_06_on_delete_restrict_on_order_master(self):
        """Verify OrderMaster cannot be deleted when child items exist."""
        order = self._create_master_order()
        item = OrderItem(
            id=1, order_id=1, organization_id=1,
            item_description="Glass", quantity=Decimal("5.000"),
            unit_price=Decimal("100.00"), total_price=Decimal("590.00")
        )
        db.session.add(item)
        db.session.commit()

        with self.assertRaises(IntegrityError):
            # Direct delete bypassing ORM cascade
            db.session.execute(text("DELETE FROM order_master WHERE id = 1;"))
            db.session.commit()
        db.session.rollback()

    def test_07_soft_deletion_and_optimistic_locking(self):
        """Verify soft deletion flag persists and version_id increments on updates."""
        order = self._create_master_order()
        self.assertEqual(order.version_id, 1)
        self.assertFalse(order.is_deleted)

        order.notes = "Updated logistics notes."
        db.session.commit()
        self.assertEqual(order.version_id, 2)

        order.is_deleted = True
        db.session.commit()
        self.assertEqual(order.version_id, 3)

        refetched = db.session.get(OrderMaster, order.id)
        self.assertTrue(refetched.is_deleted)

    def test_08_complete_oms_lifecycle_entities(self):
        """Verify instantiation and relationship navigation across all remaining OMS entities:
        Consignment, Delivery, Fitting Visit, Payment Allocation, Vendor Bill, and Vendor Payment.
        """
        order = self._create_master_order()
        item = OrderItem(
            id=1, order_id=1, organization_id=1,
            item_description="Glass Partition", quantity=Decimal("10.000"),
            unit_price=Decimal("2000.00"), total_price=Decimal("23600.00")
        )
        alloc = OrderItemVendorAllocation(
            id=1, order_id=1, order_item_id=1, vendor_id=1, organization_id=1,
            allocated_quantity=Decimal("10.000"), expected_rate=Decimal("1500.00"),
            estimated_cost=Decimal("15000.00"), created_by_id=1
        )
        po = OrderVendorProcurement(
            id=1, po_number="PO-100", order_id=1, vendor_id=1, organization_id=1,
            created_by_id=1
        )
        po_item = OrderProcurementItem(
            id=1, procurement_id=1, allocation_id=1, vendor_id=1, order_id=1, organization_id=1,
            quantity=Decimal("10.000"), unit_rate=Decimal("1500.00"), total_rate=Decimal("15000.00"),
            line_total=Decimal("17700.00")
        )
        # Consignment
        cons = OrderConsignment(
            id=1, consignment_number="CON-001", order_id=1, organization_id=1,
            transporter_name="SafeExpress", lr_number="LR-999"
        )
        cons_item = ConsignmentItem(
            id=1, consignment_id=1, procurement_item_id=1, order_id=1, organization_id=1,
            shipped_quantity=Decimal("10.000"), accepted_quantity=Decimal("10.000")
        )
        # Delivery
        delv = OrderDelivery(
            id=1, delivery_number="DEL-001", order_id=1, organization_id=1,
            challan_number="CH-001"
        )
        delv_item = DeliveryItem(
            id=1, delivery_id=1, order_id=1, order_item_id=1, consignment_item_id=1, organization_id=1,
            delivered_quantity=Decimal("10.000"), accepted_quantity=Decimal("10.000")
        )
        # Fitting Visit
        fit = OrderFittingVisit(
            id=1, visit_number="FIT-001", order_id=1, organization_id=1,
            scheduled_date=datetime.utcnow(), technician_id=2
        )
        fit_item = FittingVisitItem(
            id=1, visit_id=1, order_id=1, order_item_id=1, organization_id=1,
            planned_quantity=Decimal("10.000"), installed_quantity=Decimal("10.000")
        )
        # Payment Allocation
        payment_record = Payment(
            id=1,
            payment_number="PAY-REC-001",
            customer_name="Acme Construction",
            mobile="9876543210",
            amount=Decimal("23600.00"),
            due_date=datetime.utcnow(),
            organization_id=1,
        )
        db.session.add(payment_record)
        db.session.commit()

        pay_alloc = OrderPaymentAllocation(
            id=1, order_id=1, organization_id=1, payment_id=1,
            allocated_amount=Decimal("23600.00"), allocated_by_id=1
        )
        # Vendor Bill & 3-Way Match Item
        vbill = VendorBill(
            id=1, bill_number="VB-001", order_id=1, procurement_id=1, vendor_id=1, organization_id=1,
            bill_date=datetime.utcnow(), subtotal_amount=Decimal("15000.00"),
            tax_amount=Decimal("2700.00"), total_amount=Decimal("17700.00")
        )
        vbill_item = VendorBillItem(
            id=1, bill_id=1, procurement_item_id=1, delivery_item_id=1, order_id=1, organization_id=1,
            billed_quantity=Decimal("10.000"), unit_rate=Decimal("1500.00"),
            tax_amount=Decimal("2700.00"), total_amount=Decimal("17700.00")
        )
        # Vendor Payment
        vpay = VendorPayment(
            id=1, payment_reference="PAY-V-001", bill_id=1, vendor_id=1, order_id=1, organization_id=1,
            amount=Decimal("17700.00"), payment_mode="NEFT", created_by_id=1
        )
        # Order Document
        doc = OrderDocument(
            id=1, order_id=1, organization_id=1, document_type=OrderDocumentType.PURCHASE_ORDER.value,
            title="Approved PO Copy", file_path="/docs/po_100.pdf", uploaded_by_id=1
        )

        db.session.add_all([
            item, alloc, po, po_item,
            cons, cons_item, delv, delv_item,
            fit, fit_item, pay_alloc,
            vbill, vbill_item, vpay, doc
        ])
        db.session.commit()

        # Verify cross-relationships
        refetched_order = db.session.get(OrderMaster, 1)
        self.assertEqual(len(refetched_order.items), 1)
        self.assertEqual(len(refetched_order.procurements), 1)
        self.assertEqual(len(refetched_order.consignments), 1)
        self.assertEqual(len(refetched_order.deliveries), 1)
        self.assertEqual(len(refetched_order.fitting_visits), 1)
        self.assertEqual(len(refetched_order.payment_allocations), 1)
        self.assertEqual(len(refetched_order.documents), 1)
        self.assertEqual(vbill_item.delivery_item_id, 1)
        self.assertEqual(vbill_item.procurement_item_id, 1)
        self.assertEqual(vpay.bill.id, 1)


if __name__ == "__main__":
    unittest.main()
