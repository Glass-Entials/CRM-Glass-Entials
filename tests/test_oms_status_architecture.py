"""
Comprehensive Test Suite for Centralized OMS Status Architecture.
Verifies state machine transitions, multi-vendor aggregations,
fitting not-required auto-completion, immutable history logging, and tenant isolation.
"""

import unittest
import sys
from pathlib import Path
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from model import (
    db,
    Organization,
    User,
    UserRole,
    Employee,
    Customer,
    Lead,
    Product,
    OrderMaster,
    OrderItem,
    OrderVendorProcurement,
    OrderItemVendorAllocation,
    OrderConsignment,
    ConsignmentItem,
    OrderDelivery,
    DeliveryItem,
    OrderFittingVisit,
    FittingVisitItem,
    OrderStatusHistory,
    MasterOrderStatus,
    OrderProcurementStatus,
    OrderDeliveryStatus,
    OrderFittingStatus,
    ImmutableModelError,
)
from services.oms.status_engine import (
    OMSStatusEngine,
    OMSTransitionError,
    OMSValidationError,
    VALID_MASTER_TRANSITIONS,
    ACTIVE_MASTER_STATUSES,
)
from services.oms.order_master_service import OrderMasterService


class TestOMSStatusArchitecture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask("oms_status_test")
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            SECRET_KEY="status-arch-test-secret-key",
        )
        db.init_app(cls.app)
        cls.app_context = cls.app.app_context()
        cls.app_context.push()

    @classmethod
    def tearDownClass(cls):
        cls.app_context.pop()

    def setUp(self):
        db.create_all()
        self.org = Organization(name="Status Test Org", unique_code="STO001")
        db.session.add(self.org)
        db.session.flush()

        self.user = User(
            username="ops_admin",
            password="fakehash",
            email="ops@glassentials.com",
            role=UserRole.ADMIN,
            organization_id=self.org.id,
        )
        db.session.add(self.user)
        db.session.flush()

        self.actor = Employee(
            user_id=self.user.id,
            name="Operations Lead",
            email="ops@glassentials.com",
            organization_id=self.org.id,
        )
        db.session.add(self.actor)
        db.session.flush()

        self.customer = Customer(
            name="Metropolis Developers",
            phone_number="9876543210",
            email="metro@dev.com",
            organization_id=self.org.id,
            created_by=self.user.id,
        )
        db.session.add(self.customer)
        db.session.flush()

    def tearDown(self):
        db.session.rollback()
        db.drop_all()

    def _create_test_order(self, fitting_required=True, status=MasterOrderStatus.CONFIRMED.value):
        order = OrderMaster(
            master_order_number=f"GLS-TEST-{datetime.utcnow().strftime('%f')}",
            organization_id=self.org.id,
            customer_id=self.customer.id,
            status=status,
            procurement_status=OrderProcurementStatus.DRAFT.value,
            delivery_status=OrderDeliveryStatus.SCHEDULED.value,
            fitting_status=OrderFittingStatus.PENDING_SCHEDULE.value if fitting_required else OrderFittingStatus.NOT_REQUIRED.value,
            net_order_amount=Decimal("100000.00"),
            total_ordered_amount=Decimal("100000.00"),
            created_by_id=self.actor.id,
        )
        db.session.add(order)
        db.session.flush()

        item1 = OrderItem(
            order_id=order.id,
            organization_id=self.org.id,
            item_description="12mm Toughened Glass",
            quantity=Decimal("10.000"),
            unit_price=Decimal("5000.00"),
            total_price=Decimal("50000.00"),
        )
        item2 = OrderItem(
            order_id=order.id,
            organization_id=self.org.id,
            item_description="SS304 Spider Fittings",
            quantity=Decimal("50.000"),
            unit_price=Decimal("1000.00"),
            total_price=Decimal("50000.00"),
        )
        db.session.add_all([item1, item2])
        db.session.flush()
        return order, [item1, item2]

    # ------------------------------------------------------------------------
    # State Machine & Transition Tests
    # ------------------------------------------------------------------------
    def test_valid_forward_transitions(self):
        """Test transitioning CONFIRMED -> IN_PROCUREMENT -> IN_DELIVERY."""
        order, _ = self._create_test_order(status=MasterOrderStatus.CONFIRMED.value)

        # Transition 1: CONFIRMED -> IN_PROCUREMENT
        updated = OMSStatusEngine.transition_master_status(
            org_id=self.org.id,
            order_id=order.id,
            to_status=MasterOrderStatus.IN_PROCUREMENT.value,
            remarks="Procurement initiated",
            actor_id=self.actor.id,
        )
        self.assertEqual(updated.status, MasterOrderStatus.IN_PROCUREMENT.value)

        # Transition 2: IN_PROCUREMENT -> IN_DELIVERY
        updated2 = OMSStatusEngine.transition_master_status(
            org_id=self.org.id,
            order_id=order.id,
            to_status=MasterOrderStatus.IN_DELIVERY.value,
            remarks="Dispatched to site",
            actor_id=self.actor.id,
        )
        self.assertEqual(updated2.status, MasterOrderStatus.IN_DELIVERY.value)

    def test_illegal_transition_rejected(self):
        """Test illegal status transition raises OMSTransitionError."""
        order, _ = self._create_test_order(status=MasterOrderStatus.DRAFT.value)

        # Cannot jump from DRAFT straight to IN_DELIVERY
        with self.assertRaises(OMSTransitionError):
            OMSStatusEngine.transition_master_status(
                org_id=self.org.id,
                order_id=order.id,
                to_status=MasterOrderStatus.IN_DELIVERY.value,
                actor_id=self.actor.id,
            )

    def test_terminal_state_protection(self):
        """Test terminal states (COMPLETED, CLOSED, CANCELLED) cannot be transitioned."""
        order, items = self._create_test_order(fitting_required=False, status=MasterOrderStatus.DELIVERED.value)
        # Mark items delivered so completion is valid
        for it in items:
            it.delivered_quantity = it.quantity
        db.session.flush()

        OMSStatusEngine.transition_master_status(
            org_id=self.org.id,
            order_id=order.id,
            to_status=MasterOrderStatus.COMPLETED.value,
            actor_id=self.actor.id,
        )
        self.assertEqual(order.status, MasterOrderStatus.COMPLETED.value)

        # Attempt to transition from COMPLETED to IN_PROCUREMENT should fail
        with self.assertRaises(OMSTransitionError):
            OMSStatusEngine.transition_master_status(
                org_id=self.org.id,
                order_id=order.id,
                to_status=MasterOrderStatus.IN_PROCUREMENT.value,
                actor_id=self.actor.id,
            )

    def test_completion_prerequisites_enforced(self):
        """Test cannot complete order if items are undelivered or unfitted."""
        order, _ = self._create_test_order(fitting_required=True, status=MasterOrderStatus.DELIVERED.value)

        # Undelivered items should block COMPLETED
        with self.assertRaises(OMSValidationError):
            OMSStatusEngine.transition_master_status(
                org_id=self.org.id,
                order_id=order.id,
                to_status=MasterOrderStatus.COMPLETED.value,
                actor_id=self.actor.id,
            )

    def test_on_hold_and_resume(self):
        """Test placing order on hold and resuming back to active lifecycle."""
        order, _ = self._create_test_order(status=MasterOrderStatus.IN_PROCUREMENT.value)

        # Move to ON_HOLD
        OMSStatusEngine.transition_master_status(
            org_id=self.org.id,
            order_id=order.id,
            to_status=MasterOrderStatus.ON_HOLD.value,
            remarks="Client site water logging delay",
            actor_id=self.actor.id,
        )
        self.assertEqual(order.status, MasterOrderStatus.ON_HOLD.value)

        # Resume to IN_PROCUREMENT
        OMSStatusEngine.transition_master_status(
            org_id=self.org.id,
            order_id=order.id,
            to_status=MasterOrderStatus.IN_PROCUREMENT.value,
            remarks="Site cleared; resumed",
            actor_id=self.actor.id,
        )
        self.assertEqual(order.status, MasterOrderStatus.IN_PROCUREMENT.value)

    # ------------------------------------------------------------------------
    # Child-to-Parent Aggregation Tests
    # ------------------------------------------------------------------------
    def test_multi_vendor_procurement_aggregation_no_false_completion(self):
        """Test that procuring 1 out of 2 items does NOT mark order as fully RECEIVED."""
        order, items = self._create_test_order()
        item1, item2 = items[0], items[1]

        # Only item1 is procured
        item1.allocated_quantity = item1.quantity
        item1.procured_quantity = item1.quantity
        item2.allocated_quantity = Decimal("0")
        item2.procured_quantity = Decimal("0")
        db.session.flush()

        status = OMSStatusEngine.recalculate_procurement_status(order, actor_id=self.actor.id)
        # MUST be PARTIALLY_RECEIVED, not RECEIVED!
        self.assertEqual(status, OrderProcurementStatus.PARTIALLY_RECEIVED.value)
        self.assertEqual(order.procurement_status, OrderProcurementStatus.PARTIALLY_RECEIVED.value)

        # Now procure item2 completely
        item2.allocated_quantity = item2.quantity
        item2.procured_quantity = item2.quantity
        db.session.flush()

        status2 = OMSStatusEngine.recalculate_procurement_status(order, actor_id=self.actor.id)
        self.assertEqual(status2, OrderProcurementStatus.RECEIVED.value)

    def test_partial_delivery_aggregation(self):
        """Test partial delivery does NOT mark order DELIVERED."""
        order, items = self._create_test_order()
        item1, item2 = items[0], items[1]

        # Only item1 is delivered
        item1.delivered_quantity = item1.quantity
        item2.delivered_quantity = Decimal("0")
        db.session.flush()

        del_status = OMSStatusEngine.recalculate_delivery_status(order, actor_id=self.actor.id)
        self.assertEqual(del_status, OrderDeliveryStatus.PARTIALLY_DELIVERED.value)

        # Full delivery of all items
        item2.delivered_quantity = item2.quantity
        db.session.flush()

        del_status2 = OMSStatusEngine.recalculate_delivery_status(order, actor_id=self.actor.id)
        self.assertEqual(del_status2, OrderDeliveryStatus.DELIVERED.value)

    def test_not_required_fitting_auto_completes(self):
        """Test when fitting is NOT_REQUIRED and 100% delivered, order synchronizes to COMPLETED."""
        order, items = self._create_test_order(fitting_required=False, status=MasterOrderStatus.IN_DELIVERY.value)
        for it in items:
            it.delivered_quantity = it.quantity
        db.session.flush()

        OMSStatusEngine.synchronize_order_master_status(order, actor_id=self.actor.id)
        self.assertEqual(order.delivery_status, OrderDeliveryStatus.DELIVERED.value)
        self.assertEqual(order.fitting_status, OrderFittingStatus.NOT_REQUIRED.value)
        self.assertEqual(order.status, MasterOrderStatus.COMPLETED.value)
        self.assertIsNotNone(order.completed_at)

    def test_fitting_required_advances_to_fitting_not_completed(self):
        """Test when fitting is required and 100% delivered, order stays in DELIVERED/IN_FITTING."""
        order, items = self._create_test_order(fitting_required=True, status=MasterOrderStatus.IN_DELIVERY.value)
        for it in items:
            it.delivered_quantity = it.quantity
        db.session.flush()

        OMSStatusEngine.synchronize_order_master_status(order, actor_id=self.actor.id)
        self.assertEqual(order.delivery_status, OrderDeliveryStatus.DELIVERED.value)
        self.assertEqual(order.status, MasterOrderStatus.DELIVERED.value)

        # Now complete fitting
        for it in items:
            it.installed_quantity = it.quantity
        db.session.flush()

        OMSStatusEngine.synchronize_order_master_status(order, actor_id=self.actor.id)
        self.assertEqual(order.fitting_status, OrderFittingStatus.COMPLETED.value)
        self.assertEqual(order.status, MasterOrderStatus.COMPLETED.value)

    # ------------------------------------------------------------------------
    # Immutable History Logging Tests
    # ------------------------------------------------------------------------
    def test_status_history_logged_and_immutable(self):
        """Test every transition writes history and history cannot be modified or deleted."""
        order, _ = self._create_test_order(status=MasterOrderStatus.CONFIRMED.value)

        OMSStatusEngine.transition_master_status(
            org_id=self.org.id,
            order_id=order.id,
            to_status=MasterOrderStatus.IN_PROCUREMENT.value,
            remarks="Audit test transition",
            actor_id=self.actor.id,
        )

        history_records = OrderStatusHistory.query.filter_by(order_id=order.id).all()
        self.assertGreaterEqual(len(history_records), 1)
        latest = history_records[-1]
        self.assertEqual(latest.from_status, MasterOrderStatus.CONFIRMED.value)
        self.assertEqual(latest.to_status, MasterOrderStatus.IN_PROCUREMENT.value)
        self.assertEqual(latest.changed_by_id, self.actor.id)

        # Test immutability: updates or deletes must raise ImmutableModelError
        latest.remarks = "Tampered remarks"
        with self.assertRaises(ImmutableModelError):
            db.session.commit()
        db.session.rollback()

    def test_active_orders_scope_excludes_completed_and_closed(self):
        """Test active orders query strictly excludes completed, closed, and cancelled orders."""
        active_order, _ = self._create_test_order(status=MasterOrderStatus.IN_PROCUREMENT.value)
        comp_order, comp_items = self._create_test_order(fitting_required=False, status=MasterOrderStatus.COMPLETED.value)
        for it in comp_items:
            it.delivered_quantity = it.quantity
        db.session.flush()

        res = OrderMasterService.list_orders(org_id=self.org.id, scope="active")
        order_ids = [o.id for o in res.items]
        self.assertIn(active_order.id, order_ids)
        self.assertNotIn(comp_order.id, order_ids)


if __name__ == "__main__":
    unittest.main()
