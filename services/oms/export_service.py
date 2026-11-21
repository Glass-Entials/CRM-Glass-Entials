import csv
import io
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Tuple
from sqlalchemy import or_, and_, distinct

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from model import (
    db,
    OrderMaster,
    OrderItem,
    OrderItemVendorAllocation,
    Customer,
    Vendor,
    Employee,
)


class OMSExportService:
    """Enterprise search and tenant-isolated export generator (CSV, Excel, PDF)."""

    @classmethod
    def build_filtered_query(
        cls,
        org_id: int,
        search: Optional[str] = None,
        status: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        customer_id: Optional[int] = None,
        assigned_to_id: Optional[int] = None,
    ):
        """Construct a tenant-isolated, search-filtered query for orders."""
        query = OrderMaster.query.filter(
            OrderMaster.organization_id == org_id,
            OrderMaster.is_deleted == False,
        )

        if status:
            query = query.filter(OrderMaster.status == status)

        if from_date:
            query = query.filter(OrderMaster.order_date >= from_date)

        if to_date:
            query = query.filter(OrderMaster.order_date <= to_date)

        if customer_id:
            query = query.filter(OrderMaster.customer_id == customer_id)

        if assigned_to_id and hasattr(OrderMaster, "assigned_to_id"):
            query = query.filter(OrderMaster.assigned_to_id == assigned_to_id)

        if search:
            s = f"%{search.strip()}%"
            # Match Order Number, Title, Customer Name, or Line Items / Vendors
            query = (
                query.outerjoin(Customer, OrderMaster.customer_id == Customer.id)
                .outerjoin(OrderItem, OrderMaster.id == OrderItem.order_id)
                .outerjoin(
                    OrderItemVendorAllocation,
                    OrderItem.id == OrderItemVendorAllocation.order_item_id,
                )
                .outerjoin(Vendor, OrderItemVendorAllocation.vendor_id == Vendor.id)
                .filter(
                    or_(
                        OrderMaster.master_order_number.ilike(s),
                        OrderMaster.notes.ilike(s),
                        Customer.name.ilike(s),
                        OrderItem.item_description.ilike(s),
                        Vendor.company_name.ilike(s),
                    )
                )
                .distinct()
            )

        return query.order_by(OrderMaster.order_date.desc(), OrderMaster.id.desc())

    @classmethod
    def export_csv(
        cls,
        org_id: int,
        search: Optional[str] = None,
        status: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        customer_id: Optional[int] = None,
    ) -> bytes:
        """Generate UTF-8 CSV with BOM for universal Excel compatibility."""
        orders = cls.build_filtered_query(
            org_id=org_id,
            search=search,
            status=status,
            from_date=from_date,
            to_date=to_date,
            customer_id=customer_id,
        ).all()

        output = io.StringIO()
        # UTF-8 BOM
        output.write("\ufeff")
        writer = csv.writer(output)

        headers = [
            "Order Number",
            "Order Title",
            "Status",
            "Customer",
            "Order Date",
            "Expected Delivery",
            "Total Qty",
            "Net Revenue (INR)",
            "GST Tax (INR)",
            "Gross Total (INR)",
            "Customer Paid (INR)",
            "Customer Outstanding (INR)",
            "Procurement Cost (INR)",
            "Gross Profit (INR)",
            "Gross Margin (%)",
        ]
        writer.writerow(headers)

        for o in orders:
            gross_profit = o.net_order_amount - o.total_procurement_cost
            margin_pct = (
                round(float(gross_profit / o.net_order_amount * 100), 2)
                if o.net_order_amount > Decimal("0.00")
                else 0.0
            )
            cust_outstanding = max(Decimal("0.00"), o.total_ordered_amount - o.total_customer_paid)

            writer.writerow([
                o.master_order_number,
                o.order_title,
                o.status,
                o.customer.name if o.customer else "N/A",
                o.order_date.strftime("%Y-%m-%d") if o.order_date else "",
                o.expected_delivery_date.strftime("%Y-%m-%d") if o.expected_delivery_date else "",
                float(o.total_ordered_quantity),
                float(o.net_order_amount),
                float(o.total_tax_amount),
                float(o.total_ordered_amount),
                float(o.total_customer_paid),
                float(cust_outstanding),
                float(o.total_procurement_cost),
                float(gross_profit),
                margin_pct,
            ])

        return output.getvalue().encode("utf-8")

    @classmethod
    def export_excel(
        cls,
        org_id: int,
        search: Optional[str] = None,
        status: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        customer_id: Optional[int] = None,
    ) -> bytes:
        """Generate formatted Excel workbook (.xlsx) with formulas and styling."""
        orders = cls.build_filtered_query(
            org_id=org_id,
            search=search,
            status=status,
            from_date=from_date,
            to_date=to_date,
            customer_id=customer_id,
        ).all()

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Order Management"

        # Theme styles
        header_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
        header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
        total_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
        total_font = Font(name="Arial", size=11, bold=True, color="0F172A")
        thin_border = Border(
            left=Side(style="thin", color="E2E8F0"),
            right=Side(style="thin", color="E2E8F0"),
            top=Side(style="thin", color="E2E8F0"),
            bottom=Side(style="thin", color="E2E8F0"),
        )

        headers = [
            "Order Number",
            "Order Title",
            "Status",
            "Customer",
            "Order Date",
            "Expected Delivery",
            "Total Qty",
            "Net Revenue (₹)",
            "Total Tax (₹)",
            "Gross Total (₹)",
            "Customer Paid (₹)",
            "Outstanding (₹)",
            "Procurement Cost (₹)",
            "Gross Profit (₹)",
            "Margin %",
        ]
        ws.append(headers)

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center" if col_idx > 4 else "left", vertical="center")

        row_num = 2
        for o in orders:
            gross_profit = float(o.net_order_amount - o.total_procurement_cost)
            margin_pct = (
                round((gross_profit / float(o.net_order_amount)) * 100, 2)
                if o.net_order_amount > Decimal("0.00")
                else 0.0
            )
            cust_outstanding = float(max(Decimal("0.00"), o.total_ordered_amount - o.total_customer_paid))

            ws.append([
                o.master_order_number,
                o.order_title,
                o.status,
                o.customer.name if o.customer else "N/A",
                o.order_date.strftime("%Y-%m-%d") if o.order_date else "",
                o.expected_delivery_date.strftime("%Y-%m-%d") if o.expected_delivery_date else "",
                float(o.total_ordered_quantity),
                float(o.net_order_amount),
                float(o.total_tax_amount),
                float(o.total_ordered_amount),
                float(o.total_customer_paid),
                cust_outstanding,
                float(o.total_procurement_cost),
                gross_profit,
                margin_pct,
            ])

            for col_idx in range(1, len(headers) + 1):
                cell = ws.cell(row=row_num, column=col_idx)
                cell.border = thin_border
                # Number formatting for currency columns
                if col_idx in (8, 9, 10, 11, 12, 13, 14):
                    cell.number_format = "₹#,##0.00"
                    cell.alignment = Alignment(horizontal="right")
                elif col_idx in (7, 15):
                    cell.number_format = "#,##0.00"
                    cell.alignment = Alignment(horizontal="right")

            row_num += 1

        # Summary Row
        if len(orders) > 0:
            summary_row = [
                "TOTAL",
                f"{len(orders)} Orders",
                "",
                "",
                "",
                "",
                f"=SUM(G2:G{row_num-1})",
                f"=SUM(H2:H{row_num-1})",
                f"=SUM(I2:I{row_num-1})",
                f"=SUM(J2:J{row_num-1})",
                f"=SUM(K2:K{row_num-1})",
                f"=SUM(L2:L{row_num-1})",
                f"=SUM(M2:M{row_num-1})",
                f"=SUM(N2:N{row_num-1})",
                "",
            ]
            ws.append(summary_row)
            for col_idx in range(1, len(headers) + 1):
                cell = ws.cell(row=row_num, column=col_idx)
                cell.fill = total_fill
                cell.font = total_font
                cell.border = thin_border
                if col_idx in (8, 9, 10, 11, 12, 13, 14):
                    cell.number_format = "₹#,##0.00"
                    cell.alignment = Alignment(horizontal="right")
                elif col_idx == 7:
                    cell.number_format = "#,##0.00"
                    cell.alignment = Alignment(horizontal="right")

        # Auto-fit column widths
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        return buffer.getvalue()

    @classmethod
    def generate_order_summary_pdf(cls, org_id: int, order_id: int) -> bytes:
        """Generate high-resolution printable order summary PDF."""
        order = OrderMaster.query.filter_by(id=order_id, organization_id=org_id).first()
        if not order:
            raise ValueError(f"Order #{order_id} not found in org #{org_id}.")

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "DocTitle",
            parent=styles["Heading1"],
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#0f172a"),
            spaceAfter=12,
        )
        subtitle_style = ParagraphStyle(
            "DocSubTitle",
            parent=styles["Normal"],
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#64748b"),
            spaceAfter=16,
        )
        section_style = ParagraphStyle(
            "SectionHeader",
            parent=styles["Heading2"],
            fontSize=12,
            leading=16,
            textColor=colors.HexColor("#0369a1"),
            spaceBefore=14,
            spaceAfter=8,
        )
        body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=9, leading=12)

        elements = []

        # Header
        elements.append(Paragraph(f"GlassEntials OMS — Order Summary", title_style))
        elements.append(
            Paragraph(
                f"Master Order Number: <b>{order.master_order_number}</b> | "
                f"Generated on: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
                subtitle_style,
            )
        )

        # Order Meta Table
        order_meta_data = [
            ["Order Title:", order.order_title, "Order Status:", order.status],
            [
                "Customer:",
                order.customer.name if order.customer else "N/A",
                "Order Date:",
                order.order_date.strftime("%Y-%m-%d") if order.order_date else "N/A",
            ],
            [
                "Delivery Site:",
                getattr(order, "site_address", None) or getattr(order, "shipping_address", None) or "Site Address Not Set",
                "Expected Delivery:",
                order.expected_delivery_date.strftime("%Y-%m-%d") if order.expected_delivery_date else "N/A",
            ],
        ]
        meta_table = Table(order_meta_data, colWidths=[110, 160, 110, 140])
        meta_table.setStyle(
            TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#475569")),
                ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#475569")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#e2e8f0")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#f1f5f9")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ])
        )
        elements.append(meta_table)
        elements.append(Spacer(1, 14))

        # Items Table
        elements.append(Paragraph("Ordered Items & Quantities", section_style))
        items_data = [["#", "Item Description", "Qty", "Unit Price (₹)", "Tax %", "Total Price (₹)"]]
        for idx, it in enumerate(order.items, 1):
            if it.is_deleted:
                continue
            items_data.append([
                str(idx),
                it.item_description,
                f"{float(it.quantity):.3f}",
                f"{float(it.unit_price):,.2f}",
                f"{float(it.tax_rate):.1f}%",
                f"{float(it.total_price):,.2f}",
            ])

        items_table = Table(items_data, colWidths=[24, 230, 60, 70, 50, 86])
        items_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ])
        )
        elements.append(items_table)
        elements.append(Spacer(1, 14))

        # Financial Summary Table
        elements.append(Paragraph("Commercial & Financial Reconciliation", section_style))
        gross_profit = order.net_order_amount - order.total_procurement_cost
        margin_pct = (
            round(float(gross_profit / order.net_order_amount * 100), 2)
            if order.net_order_amount > Decimal("0.00")
            else 0.0
        )
        cust_outstanding = max(Decimal("0.00"), order.total_ordered_amount - order.total_customer_paid)

        fin_data = [
            ["Net Order Revenue:", f"₹{float(order.net_order_amount):,.2f}", "Total Procurement Cost:", f"₹{float(order.total_procurement_cost):,.2f}"],
            ["Total Tax Amount:", f"₹{float(order.total_tax_amount):,.2f}", "Estimated Gross Profit:", f"₹{float(gross_profit):,.2f} ({margin_pct}%)"],
            ["Gross Order Value:", f"₹{float(order.total_ordered_amount):,.2f}", "Customer Paid:", f"₹{float(order.total_customer_paid):,.2f}"],
            ["Customer Outstanding:", f"₹{float(cust_outstanding):,.2f}", "Payment Status:", order.customer_payment_status],
        ]
        fin_table = Table(fin_data, colWidths=[130, 130, 130, 130])
        fin_table.setStyle(
            TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f0f9ff")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#bae6fd")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e0f2fe")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ])
        )
        elements.append(fin_table)

        doc.build(elements)
        buffer.seek(0)
        return buffer.getvalue()
