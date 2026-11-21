"""add_oms_database_foundation

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-11-21 14:15:00.000000

Order Management System (OMS) Phase 1 Database Foundation:
- Enforces multi-tenant ownership via composite unique constraints and RESTRICT foreign keys.
- Creates 19 approved OMS entities in strict dependency order:
  1. order_master
  2. order_item
  3. order_vendor_procurement
  4. order_item_vendor_allocation
  5. order_procurement_item
  6. order_consignment
  7. consignment_item
  8. order_delivery
  9. delivery_item
  10. order_fitting_visit
  11. fitting_visit_item
  12. order_payment_allocation
  13. vendor_bill
  14. vendor_bill_item
  15. vendor_payment
  16. order_document
  17. order_revision
  18. order_status_history
  19. order_exceptional_closure
- Implements immutable audit triggers on MySQL for order_status_history, order_revision, and order_exceptional_closure.
- Provides clean, safe downgrade operations.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    # ── 1. order_master ──
    op.create_table(
        'order_master',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('master_order_number', sa.String(length=50), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('quotation_id', sa.Integer(), nullable=True),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='DRAFT'),
        sa.Column('procurement_status', sa.String(length=50), nullable=False, server_default='DRAFT'),
        sa.Column('delivery_status', sa.String(length=50), nullable=False, server_default='SCHEDULED'),
        sa.Column('fitting_status', sa.String(length=50), nullable=False, server_default='NOT_REQUIRED'),
        sa.Column('customer_payment_status', sa.String(length=50), nullable=False, server_default='UNPAID'),
        sa.Column('vendor_payment_status', sa.String(length=50), nullable=False, server_default='UNPAID'),
        sa.Column('total_ordered_amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('total_tax_amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('total_discount_amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('net_order_amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('total_procurement_cost', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('total_customer_paid', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('total_vendor_billed', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('total_vendor_paid', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('order_date', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('expected_delivery_date', sa.DateTime(), nullable=True),
        sa.Column('actual_delivery_date', sa.DateTime(), nullable=True),
        sa.Column('expected_fitting_date', sa.DateTime(), nullable=True),
        sa.Column('actual_fitting_date', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(), nullable=True),
        sa.Column('shipping_address', sa.Text(), nullable=True),
        sa.Column('billing_address', sa.Text(), nullable=True),
        sa.Column('site_address', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('version_id', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('assigned_to_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'master_order_number', name='uq_order_master_org_num'),
        sa.UniqueConstraint('id', 'organization_id', name='uq_order_master_id_org'),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_order_master_org', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name='fk_order_master_cust', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['quotation_id'], ['quotation.id'], name='fk_order_master_quot', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['project_id'], ['project.id'], name='fk_order_master_proj', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by_id'], ['employee.id'], name='fk_order_master_creator', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['assigned_to_id'], ['employee.id'], name='fk_order_master_assignee', ondelete='RESTRICT'),
    )
    op.create_index('ix_order_master_org_date', 'order_master', ['organization_id', 'order_date'])
    op.create_index('ix_order_master_status', 'order_master', ['status'])

    # ── 2. order_item ──
    op.create_table(
        'order_item',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=True),
        sa.Column('item_description', sa.Text(), nullable=False),
        sa.Column('specifications', sa.Text(), nullable=True),
        sa.Column('quantity', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('unit_price', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('tax_rate', sa.Numeric(precision=5, scale=2), nullable=False, server_default='0.00'),
        sa.Column('tax_amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('discount_amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('total_price', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('allocated_quantity', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0.000'),
        sa.Column('procured_quantity', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0.000'),
        sa.Column('delivered_quantity', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0.000'),
        sa.Column('installed_quantity', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0.000'),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_order_item_id_order_org'),
        sa.ForeignKeyConstraint(
            ['order_id', 'organization_id'],
            ['order_master.id', 'order_master.organization_id'],
            name='fk_order_item_order_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_order_item_org', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['product_id'], ['product.id'], name='fk_order_item_prod', ondelete='RESTRICT'),
    )
    op.create_index('ix_order_item_order_org', 'order_item', ['order_id', 'organization_id'])

    # ── 3. order_vendor_procurement ──
    op.create_table(
        'order_vendor_procurement',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('po_number', sa.String(length=50), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('vendor_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='DRAFT'),
        sa.Column('po_date', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('expected_delivery_date', sa.DateTime(), nullable=True),
        sa.Column('actual_delivery_date', sa.DateTime(), nullable=True),
        sa.Column('total_procurement_amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('tax_amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('net_procurement_amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'po_number', name='uq_proc_org_num'),
        sa.UniqueConstraint('id', 'vendor_id', 'order_id', 'organization_id', name='uq_proc_id_vendor_order_org'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_proc_id_order_org'),
        sa.ForeignKeyConstraint(
            ['order_id', 'organization_id'],
            ['order_master.id', 'order_master.organization_id'],
            name='fk_proc_order_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['vendor_id'], ['vendor.id'], name='fk_proc_vendor', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_proc_org', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by_id'], ['employee.id'], name='fk_proc_creator', ondelete='RESTRICT'),
    )
    op.create_index('ix_proc_order_vendor', 'order_vendor_procurement', ['order_id', 'vendor_id'])

    # ── 4. order_item_vendor_allocation ──
    op.create_table(
        'order_item_vendor_allocation',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('order_item_id', sa.Integer(), nullable=False),
        sa.Column('vendor_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('procurement_id', sa.Integer(), nullable=True),
        sa.Column('allocated_quantity', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('expected_rate', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('estimated_cost', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('received_quantity', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0.000'),
        sa.Column('expected_supply_date', sa.DateTime(), nullable=True),
        sa.Column('actual_supply_date', sa.DateTime(), nullable=True),
        sa.Column('allocation_status', sa.String(length=50), nullable=False, server_default='PENDING'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'vendor_id', 'order_id', 'organization_id', name='uq_alloc_id_vendor_order_org'),
        sa.UniqueConstraint('id', 'order_item_id', 'order_id', 'organization_id', name='uq_alloc_id_item_order_org'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_alloc_id_order_org'),
        sa.ForeignKeyConstraint(
            ['order_id', 'organization_id'],
            ['order_master.id', 'order_master.organization_id'],
            name='fk_alloc_order_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['order_item_id', 'order_id', 'organization_id'],
            ['order_item.id', 'order_item.order_id', 'order_item.organization_id'],
            name='fk_alloc_order_item_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['vendor_id'], ['vendor.id'], name='fk_alloc_vendor', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_alloc_org', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['procurement_id'], ['order_vendor_procurement.id'], name='fk_alloc_proc', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by_id'], ['employee.id'], name='fk_alloc_creator', ondelete='RESTRICT'),
    )
    op.create_index('ix_alloc_item_vendor', 'order_item_vendor_allocation', ['order_item_id', 'vendor_id'])

    # ── 5. order_procurement_item ──
    op.create_table(
        'order_procurement_item',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('procurement_id', sa.Integer(), nullable=False),
        sa.Column('allocation_id', sa.Integer(), nullable=False),
        sa.Column('vendor_id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('unit_rate', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('total_rate', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('tax_amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('line_total', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('received_quantity', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0.000'),
        sa.Column('accepted_quantity', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0.000'),
        sa.Column('rejected_quantity', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0.000'),
        sa.Column('billed_quantity', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0.000'),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_proc_item_id_order_org'),
        sa.UniqueConstraint('id', 'procurement_id', 'order_id', 'organization_id', name='uq_proc_item_id_proc_order_org'),
        sa.UniqueConstraint('procurement_id', 'allocation_id', name='uq_proc_item_proc_alloc'),
        sa.ForeignKeyConstraint(
            ['procurement_id', 'vendor_id', 'order_id', 'organization_id'],
            ['order_vendor_procurement.id', 'order_vendor_procurement.vendor_id', 'order_vendor_procurement.order_id', 'order_vendor_procurement.organization_id'],
            name='fk_proc_item_proc_vendor',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['allocation_id', 'vendor_id', 'order_id', 'organization_id'],
            ['order_item_vendor_allocation.id', 'order_item_vendor_allocation.vendor_id', 'order_item_vendor_allocation.order_id', 'order_item_vendor_allocation.organization_id'],
            name='fk_proc_item_alloc_vendor',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_proc_item_org', ondelete='RESTRICT'),
    )
    op.create_index('ix_proc_item_proc', 'order_procurement_item', ['procurement_id'])

    # ── 6. order_consignment ──
    op.create_table(
        'order_consignment',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('consignment_number', sa.String(length=50), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('procurement_id', sa.Integer(), nullable=True),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('transporter_name', sa.String(length=100), nullable=True),
        sa.Column('lr_number', sa.String(length=50), nullable=True),
        sa.Column('vehicle_number', sa.String(length=50), nullable=True),
        sa.Column('dispatch_date', sa.DateTime(), nullable=True),
        sa.Column('expected_arrival_date', sa.DateTime(), nullable=True),
        sa.Column('actual_arrival_date', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='DISPATCHED'),
        sa.Column('shipping_cost', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('received_by_id', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_consignment_id_order_org'),
        sa.UniqueConstraint('organization_id', 'consignment_number', name='uq_consignment_org_num'),
        sa.ForeignKeyConstraint(
            ['order_id', 'organization_id'],
            ['order_master.id', 'order_master.organization_id'],
            name='fk_consignment_order_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['procurement_id'], ['order_vendor_procurement.id'], name='fk_consignment_proc', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_consignment_org', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['received_by_id'], ['employee.id'], name='fk_consignment_receiver', ondelete='RESTRICT'),
    )
    op.create_index('ix_consignment_order', 'order_consignment', ['order_id'])

    # ── 7. consignment_item ──
    op.create_table(
        'consignment_item',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('consignment_id', sa.Integer(), nullable=False),
        sa.Column('procurement_item_id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('shipped_quantity', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('received_quantity', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0.000'),
        sa.Column('accepted_quantity', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0.000'),
        sa.Column('damaged_quantity', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0.000'),
        sa.Column('rejected_quantity', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0.000'),
        sa.Column('qc_status', sa.String(length=50), nullable=False, server_default='PENDING'),
        sa.Column('qc_remarks', sa.Text(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_cons_item_id_order_org'),
        sa.ForeignKeyConstraint(
            ['consignment_id', 'order_id', 'organization_id'],
            ['order_consignment.id', 'order_consignment.order_id', 'order_consignment.organization_id'],
            name='fk_cons_item_cons_order_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['procurement_item_id', 'order_id', 'organization_id'],
            ['order_procurement_item.id', 'order_procurement_item.order_id', 'order_procurement_item.organization_id'],
            name='fk_cons_item_proc_item',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_cons_item_org', ondelete='RESTRICT'),
    )
    op.create_index('ix_cons_item_consignment', 'consignment_item', ['consignment_id'])

    # ── 8. order_delivery ──
    op.create_table(
        'order_delivery',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('delivery_number', sa.String(length=50), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('delivery_date', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('recipient_name', sa.String(length=100), nullable=True),
        sa.Column('recipient_phone', sa.String(length=20), nullable=True),
        sa.Column('delivery_address', sa.Text(), nullable=True),
        sa.Column('challan_number', sa.String(length=50), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='SCHEDULED'),
        sa.Column('delivered_by_id', sa.Integer(), nullable=True),
        sa.Column('proof_of_delivery_document_id', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_delivery_id_order_org'),
        sa.UniqueConstraint('organization_id', 'delivery_number', name='uq_delivery_org_num'),
        sa.ForeignKeyConstraint(
            ['order_id', 'organization_id'],
            ['order_master.id', 'order_master.organization_id'],
            name='fk_delivery_order_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['delivered_by_id'], ['employee.id'], name='fk_delivery_driver', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_delivery_org', ondelete='RESTRICT'),
    )
    op.create_index('ix_delivery_order', 'order_delivery', ['order_id'])

    # ── 9. delivery_item ──
    op.create_table(
        'delivery_item',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('delivery_id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('order_item_id', sa.Integer(), nullable=False),
        sa.Column('consignment_item_id', sa.Integer(), nullable=True),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('delivered_quantity', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('accepted_quantity', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('damaged_quantity', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0.000'),
        sa.Column('returned_quantity', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0.000'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_del_item_id_order_org'),
        sa.ForeignKeyConstraint(
            ['delivery_id', 'order_id', 'organization_id'],
            ['order_delivery.id', 'order_delivery.order_id', 'order_delivery.organization_id'],
            name='fk_del_item_del_order_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['order_item_id', 'order_id', 'organization_id'],
            ['order_item.id', 'order_item.order_id', 'order_item.organization_id'],
            name='fk_del_item_order_item',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['consignment_item_id', 'order_id', 'organization_id'],
            ['consignment_item.id', 'consignment_item.order_id', 'consignment_item.organization_id'],
            name='fk_del_item_cons_item',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_del_item_org', ondelete='RESTRICT'),
    )
    op.create_index('ix_del_item_delivery', 'delivery_item', ['delivery_id'])

    # ── 10. order_fitting_visit ──
    op.create_table(
        'order_fitting_visit',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('visit_number', sa.String(length=50), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('technician_id', sa.Integer(), nullable=True),
        sa.Column('scheduled_date', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='SCHEDULED'),
        sa.Column('client_signoff', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('signoff_document_id', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_fitting_id_order_org'),
        sa.UniqueConstraint('organization_id', 'visit_number', name='uq_fitting_org_num'),
        sa.ForeignKeyConstraint(
            ['order_id', 'organization_id'],
            ['order_master.id', 'order_master.organization_id'],
            name='fk_fitting_order_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['technician_id'], ['employee.id'], name='fk_fitting_tech', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_fitting_org', ondelete='RESTRICT'),
    )
    op.create_index('ix_fitting_order', 'order_fitting_visit', ['order_id'])

    # ── 11. fitting_visit_item ──
    op.create_table(
        'fitting_visit_item',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('visit_id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('order_item_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('planned_quantity', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('installed_quantity', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0.000'),
        sa.Column('remaining_quantity', sa.Numeric(precision=12, scale=3), nullable=False, server_default='0.000'),
        sa.Column('issues_reported', sa.Text(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_fit_item_id_order_org'),
        sa.UniqueConstraint('visit_id', 'order_item_id', name='uq_fit_item_visit_item'),
        sa.ForeignKeyConstraint(
            ['visit_id', 'order_id', 'organization_id'],
            ['order_fitting_visit.id', 'order_fitting_visit.order_id', 'order_fitting_visit.organization_id'],
            name='fk_fit_item_visit_order_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['order_item_id', 'order_id', 'organization_id'],
            ['order_item.id', 'order_item.order_id', 'order_item.organization_id'],
            name='fk_fit_item_order_item',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_fit_item_org', ondelete='RESTRICT'),
    )
    op.create_index('ix_fit_item_visit', 'fitting_visit_item', ['visit_id'])

    # ── 12. order_payment_allocation ──
    op.create_table(
        'order_payment_allocation',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('payment_id', sa.Integer(), nullable=False),
        sa.Column('invoice_id', sa.Integer(), nullable=True),
        sa.Column('allocation_type', sa.String(length=50), nullable=False, server_default='DIRECT_PAYMENT'),
        sa.Column('allocated_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('allocation_date', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('allocated_by_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_pay_alloc_id_order_org'),
        sa.ForeignKeyConstraint(
            ['order_id', 'organization_id'],
            ['order_master.id', 'order_master.organization_id'],
            name='fk_payment_alloc_order_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['payment_id'], ['payment.id'], name='fk_payment_alloc_pmt', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoice.id'], name='fk_payment_alloc_inv', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_payment_alloc_org', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['allocated_by_id'], ['employee.id'], name='fk_payment_alloc_allocator', ondelete='RESTRICT'),
    )
    op.create_index('ix_pay_alloc_order', 'order_payment_allocation', ['order_id'])

    # ── 13. vendor_bill ──
    op.create_table(
        'vendor_bill',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('bill_number', sa.String(length=50), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('procurement_id', sa.Integer(), nullable=False),
        sa.Column('vendor_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('bill_date', sa.DateTime(), nullable=False),
        sa.Column('due_date', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='DRAFT'),
        sa.Column('subtotal_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('tax_amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('total_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('paid_amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('approved_by_id', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_vbill_id_order_org'),
        sa.UniqueConstraint('id', 'vendor_id', 'order_id', 'organization_id', name='uq_vbill_id_vendor_order_org'),
        sa.UniqueConstraint('organization_id', 'vendor_id', 'bill_number', name='uq_vbill_org_vendor_num'),
        sa.ForeignKeyConstraint(
            ['order_id', 'organization_id'],
            ['order_master.id', 'order_master.organization_id'],
            name='fk_vbill_order_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['procurement_id', 'vendor_id', 'order_id', 'organization_id'],
            ['order_vendor_procurement.id', 'order_vendor_procurement.vendor_id', 'order_vendor_procurement.order_id', 'order_vendor_procurement.organization_id'],
            name='fk_vbill_proc_vendor',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['vendor_id'], ['vendor.id'], name='fk_vbill_vendor', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_vbill_org', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['approved_by_id'], ['employee.id'], name='fk_vbill_approver', ondelete='RESTRICT'),
    )
    op.create_index('ix_vbill_order_vendor', 'vendor_bill', ['order_id', 'vendor_id'])

    # ── 14. vendor_bill_item ──
    op.create_table(
        'vendor_bill_item',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('bill_id', sa.Integer(), nullable=False),
        sa.Column('procurement_item_id', sa.Integer(), nullable=False),
        sa.Column('delivery_item_id', sa.Integer(), nullable=True),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('billed_quantity', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('unit_rate', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('tax_amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0.00'),
        sa.Column('total_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_vbill_item_id_order_org'),
        sa.ForeignKeyConstraint(
            ['bill_id', 'order_id', 'organization_id'],
            ['vendor_bill.id', 'vendor_bill.order_id', 'vendor_bill.organization_id'],
            name='fk_vbill_item_bill_order_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['procurement_item_id', 'order_id', 'organization_id'],
            ['order_procurement_item.id', 'order_procurement_item.order_id', 'order_procurement_item.organization_id'],
            name='fk_vbill_item_proc_item',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['delivery_item_id', 'order_id', 'organization_id'],
            ['delivery_item.id', 'delivery_item.order_id', 'delivery_item.organization_id'],
            name='fk_vbill_item_del_item',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_vbill_item_org', ondelete='RESTRICT'),
    )
    op.create_index('ix_vbill_item_bill', 'vendor_bill_item', ['bill_id'])

    # ── 15. vendor_payment ──
    op.create_table(
        'vendor_payment',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('payment_reference', sa.String(length=50), nullable=False),
        sa.Column('bill_id', sa.Integer(), nullable=False),
        sa.Column('vendor_id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('payment_date', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('payment_mode', sa.String(length=50), nullable=False),
        sa.Column('transaction_reference', sa.String(length=100), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_vpay_id_order_org'),
        sa.UniqueConstraint('organization_id', 'payment_reference', name='uq_vpay_org_ref'),
        sa.ForeignKeyConstraint(
            ['bill_id', 'vendor_id', 'order_id', 'organization_id'],
            ['vendor_bill.id', 'vendor_bill.vendor_id', 'vendor_bill.order_id', 'vendor_bill.organization_id'],
            name='fk_vpay_bill_vendor',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['order_id', 'organization_id'],
            ['order_master.id', 'order_master.organization_id'],
            name='fk_vpay_order_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['vendor_id'], ['vendor.id'], name='fk_vpay_vendor', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_vpay_org', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by_id'], ['employee.id'], name='fk_vpay_creator', ondelete='RESTRICT'),
    )
    op.create_index('ix_vpay_bill', 'vendor_payment', ['bill_id'])

    # ── 16. order_document ──
    op.create_table(
        'order_document',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('document_type', sa.String(length=50), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('file_path', sa.String(length=500), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('mime_type', sa.String(length=100), nullable=True),
        sa.Column('is_customer_visible', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('uploaded_by_id', sa.Integer(), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_doc_id_order_org'),
        sa.ForeignKeyConstraint(
            ['order_id', 'organization_id'],
            ['order_master.id', 'order_master.organization_id'],
            name='fk_doc_order_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_doc_org', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['uploaded_by_id'], ['employee.id'], name='fk_doc_uploader', ondelete='RESTRICT'),
    )
    op.create_index('ix_doc_order_type', 'order_document', ['order_id', 'document_type'])

    # ── 17. order_revision ──
    op.create_table(
        'order_revision',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('revision_number', sa.Integer(), nullable=False),
        sa.Column('snapshot_json', sa.Text(), nullable=False),
        sa.Column('change_reason', sa.Text(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_id', 'revision_number', name='uq_rev_order_num'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_rev_id_order_org'),
        sa.ForeignKeyConstraint(
            ['order_id', 'organization_id'],
            ['order_master.id', 'order_master.organization_id'],
            name='fk_rev_order_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_rev_org', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by_id'], ['employee.id'], name='fk_rev_creator', ondelete='RESTRICT'),
    )
    op.create_index('ix_rev_order', 'order_revision', ['order_id'])

    # ── 18. order_status_history ──
    op.create_table(
        'order_status_history',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('status_type', sa.String(length=50), nullable=False, server_default='MASTER'),
        sa.Column('from_status', sa.String(length=50), nullable=True),
        sa.Column('to_status', sa.String(length=50), nullable=False),
        sa.Column('remarks', sa.Text(), nullable=True),
        sa.Column('changed_by_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_status_hist_id_order_org'),
        sa.ForeignKeyConstraint(
            ['order_id', 'organization_id'],
            ['order_master.id', 'order_master.organization_id'],
            name='fk_status_hist_order_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_status_hist_org', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['changed_by_id'], ['employee.id'], name='fk_status_hist_changer', ondelete='RESTRICT'),
    )
    op.create_index('ix_status_hist_order', 'order_status_history', ['order_id'])

    # ── 19. order_exceptional_closure ──
    op.create_table(
        'order_exceptional_closure',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('closure_type', sa.String(length=50), nullable=False, server_default='CANCELLED'),
        sa.Column('closure_reason', sa.Text(), nullable=False),
        sa.Column('financial_impact_notes', sa.Text(), nullable=True),
        sa.Column('approved_by_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_id', name='uq_closure_order_id'),
        sa.UniqueConstraint('id', 'order_id', 'organization_id', name='uq_closure_id_order_org'),
        sa.ForeignKeyConstraint(
            ['order_id', 'organization_id'],
            ['order_master.id', 'order_master.organization_id'],
            name='fk_closure_order_org',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_closure_org', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['approved_by_id'], ['employee.id'], name='fk_closure_approver', ondelete='RESTRICT'),
    )

    # ── Immutable Audit Triggers for MySQL Dialect ──
    bind = op.get_bind()
    if bind.dialect.name == 'mysql':
        op.execute("""
        CREATE TRIGGER trg_order_status_history_no_update
        BEFORE UPDATE ON order_status_history
        FOR EACH ROW
        BEGIN
            SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'order_status_history records are immutable and cannot be updated.';
        END;
        """)
        op.execute("""
        CREATE TRIGGER trg_order_status_history_no_delete
        BEFORE DELETE ON order_status_history
        FOR EACH ROW
        BEGIN
            SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'order_status_history records are immutable and cannot be deleted.';
        END;
        """)
        op.execute("""
        CREATE TRIGGER trg_order_exceptional_closure_no_update
        BEFORE UPDATE ON order_exceptional_closure
        FOR EACH ROW
        BEGIN
            SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'order_exceptional_closure records are immutable and cannot be updated.';
        END;
        """)
        op.execute("""
        CREATE TRIGGER trg_order_exceptional_closure_no_delete
        BEFORE DELETE ON order_exceptional_closure
        FOR EACH ROW
        BEGIN
            SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'order_exceptional_closure records are immutable and cannot be deleted.';
        END;
        """)
        op.execute("""
        CREATE TRIGGER trg_order_revision_no_update
        BEFORE UPDATE ON order_revision
        FOR EACH ROW
        BEGIN
            SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'order_revision records are immutable and cannot be updated.';
        END;
        """)
        op.execute("""
        CREATE TRIGGER trg_order_revision_no_delete
        BEFORE DELETE ON order_revision
        FOR EACH ROW
        BEGIN
            SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'order_revision records are immutable and cannot be deleted.';
        END;
        """)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'mysql':
        op.execute("DROP TRIGGER IF EXISTS trg_order_revision_no_delete;")
        op.execute("DROP TRIGGER IF EXISTS trg_order_revision_no_update;")
        op.execute("DROP TRIGGER IF EXISTS trg_order_exceptional_closure_no_delete;")
        op.execute("DROP TRIGGER IF EXISTS trg_order_exceptional_closure_no_update;")
        op.execute("DROP TRIGGER IF EXISTS trg_order_status_history_no_delete;")
        op.execute("DROP TRIGGER IF EXISTS trg_order_status_history_no_update;")

    # Drop in exact reverse dependency order
    op.drop_table('order_exceptional_closure')
    op.drop_table('order_status_history')
    op.drop_table('order_revision')
    op.drop_table('order_document')
    op.drop_table('vendor_payment')
    op.drop_table('vendor_bill_item')
    op.drop_table('vendor_bill')
    op.drop_table('order_payment_allocation')
    op.drop_table('fitting_visit_item')
    op.drop_table('order_fitting_visit')
    op.drop_table('delivery_item')
    op.drop_table('order_delivery')
    op.drop_table('consignment_item')
    op.drop_table('order_consignment')
    op.drop_table('order_procurement_item')
    op.drop_table('order_item_vendor_allocation')
    op.drop_table('order_vendor_procurement')
    op.drop_table('order_item')
    op.drop_table('order_master')
