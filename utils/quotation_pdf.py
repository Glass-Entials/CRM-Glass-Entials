"""
PDF Generator for Quotations using xhtml2pdf (pisa).
Falls back gracefully if library not installed.
"""
import io
from flask import render_template_string
from jinja2 import Environment, FileSystemLoader
import os


def render_quotation_pdf(quotation, settings, term_sections, signature):
    """
    Render quotation as a PDF bytes object.
    Uses xhtml2pdf which is pure-Python and works on Windows without GTK.
    """
    try:
        from xhtml2pdf import pisa
    except ImportError:
        raise RuntimeError(
            "xhtml2pdf is not installed. Run: pip install xhtml2pdf"
        )

    # Render the HTML template to string
    template_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'templates', 'accounts', 'quotation_pdf.html'
    )

    env = Environment(
        loader=FileSystemLoader(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')
        )
    )
    template = env.get_template('accounts/quotation_pdf.html')
    html_str = template.render(
        quotation     = quotation,
        settings      = settings,
        term_sections = term_sections,
        signature     = signature,
    )

    # Convert to PDF
    pdf_buffer = io.BytesIO()
    base_url   = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')

    result = pisa.CreatePDF(
        html_str,
        dest         = pdf_buffer,
        encoding     = 'utf-8',
        path         = base_url,
    )

    if result.err:
        raise RuntimeError(f"PDF generation failed with {result.err} errors")

    pdf_buffer.seek(0)
    return pdf_buffer.read()
