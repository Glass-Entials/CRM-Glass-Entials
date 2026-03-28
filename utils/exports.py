import csv
import pandas as pd
from io import BytesIO, StringIO
from datetime import datetime
from fpdf import FPDF
from flask import Response, send_file

def export_to_csv(data, headers, filename):
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(headers)
    for row in data:
        cw.writerow([row[h.lower()] for h in headers])
    return Response(
        si.getvalue(),
        mimetype='text/csv',
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )

def export_to_excel(data, headers, filename):
    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Data')
        workbook = writer.book
        worksheet = writer.sheets['Data']
        
        header_fmt = workbook.add_format({
            'bold': True, 'bg_color': '#D4AF37', 'font_color': '#FFFFFF', 
            'border': 1, 'align': 'center', 'valign': 'middle'
        })
        for col_num, value in enumerate(headers):
            worksheet.write(0, col_num, value, header_fmt)
            worksheet.set_column(col_num, col_num, 20)
        
        worksheet.freeze_panes(1, 0)
    
    output.seek(0)
    return send_file(
        output, as_attachment=True, download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

def export_to_pdf(data, headers, filename, title="Management Report"):
    class PDF(FPDF):
        def footer(self):
            self.set_y(-15)
            self.set_font('helvetica', 'I', 8)
            self.set_text_color(128)
            self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

    pdf = PDF(orientation='L', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("helvetica", 'B', 16)
    pdf.set_text_color(13, 27, 42)
    pdf.cell(0, 15, title, 0, 1, 'L')
    pdf.set_font("helvetica", size=9)
    pdf.set_text_color(100)
    pdf.cell(0, 5, f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", 0, 1, 'L')
    pdf.ln(10)
    
    # Calculate widths based on header count
    col_width = 270 / len(headers)
    pdf.set_font("helvetica", 'B', 7)
    pdf.set_fill_color(212, 175, 55)
    pdf.set_text_color(255)
    for header in headers:
        pdf.cell(col_width, 8, header, border=1, fill=True, align='C')
    pdf.ln()
    
    pdf.set_font("helvetica", size=7)
    pdf.set_text_color(0)
    for idx, row in enumerate(data):
        pdf.set_fill_color(248, 249, 250) if idx % 2 == 0 else pdf.set_fill_color(255, 255, 255)
        for header in headers:
            val = str(row.get(header.lower(), ''))[:25]
            pdf.cell(col_width, 8, val, border=1, fill=True, align='L')
        pdf.ln()
        
    pdf_output = pdf.output()
    buffer = BytesIO(pdf_output)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')
