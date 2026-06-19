"""Generate two sample invoices: one with a text layer (pdftotext
works), one that's an image-only PDF (forces tesseract OCR path).
Both have the SAME content so they're a controlled OCR test pair.

Usage:
  python generate_invoices.py <output-folder>
"""
import io
import sys
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from PIL import Image, ImageDraw, ImageFont

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)

VENDOR = "Contoso Office Supplies, Inc."
VENDOR_ADDR = ["123 Market Street", "Seattle, WA 98101"]
BILL_TO = "Fabrikam Engineering, LLC"
BILL_TO_ADDR = ["456 Innovation Drive", "Bellevue, WA 98004"]
NUMBER = "INV-2026-00427"
DATE = "2026-05-29"
DUE = "2026-06-28"
ITEMS = [
    ("Ergonomic mesh chair (model EX-200)", 4, 249.00),
    ("Standing desk - electric, walnut top", 2, 539.50),
    ("Webcam - 4K with ring light",          6,  89.99),
    ("USB-C dock with 100W passthrough",     8, 145.00),
]
TAX_RATE = 0.087  # 8.7% WA sales tax


def draw_invoice_pdf(c: canvas.Canvas) -> None:
    width, height = LETTER
    c.setFont("Helvetica-Bold", 24)
    c.drawString(0.75 * inch, height - 1 * inch, "INVOICE")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(0.75 * inch, height - 1.5 * inch, VENDOR)
    c.setFont("Helvetica", 10)
    for i, line in enumerate(VENDOR_ADDR):
        c.drawString(0.75 * inch, height - 1.7 * inch - i * 0.18 * inch, line)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(4.5 * inch, height - 1.5 * inch, "Bill to:")
    c.drawString(4.5 * inch, height - 1.7 * inch, BILL_TO)
    c.setFont("Helvetica", 10)
    for i, line in enumerate(BILL_TO_ADDR):
        c.drawString(4.5 * inch, height - 1.9 * inch - i * 0.18 * inch, line)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(0.75 * inch, height - 2.6 * inch, f"Invoice Number: {NUMBER}")
    c.drawString(0.75 * inch, height - 2.8 * inch, f"Invoice Date:   {DATE}")
    c.drawString(0.75 * inch, height - 3.0 * inch, f"Due Date:       {DUE}")
    c.drawString(0.75 * inch, height - 3.2 * inch, f"Currency:       USD")
    y = height - 4.0 * inch
    c.setFont("Helvetica-Bold", 10)
    c.drawString(0.75 * inch, y, "Description")
    c.drawString(4.6 * inch, y, "Qty")
    c.drawString(5.3 * inch, y, "Unit Price")
    c.drawString(6.7 * inch, y, "Amount")
    c.line(0.75 * inch, y - 0.06 * inch, 7.7 * inch, y - 0.06 * inch)
    c.setFont("Helvetica", 10)
    subtotal = 0.0
    for desc, qty, unit in ITEMS:
        y -= 0.28 * inch
        amount = qty * unit
        subtotal += amount
        c.drawString(0.75 * inch, y, desc)
        c.drawString(4.6 * inch, y, str(qty))
        c.drawString(5.3 * inch, y, f"${unit:,.2f}")
        c.drawString(6.7 * inch, y, f"${amount:,.2f}")
    tax = round(subtotal * TAX_RATE, 2)
    total = round(subtotal + tax, 2)
    y -= 0.5 * inch
    c.line(5.3 * inch, y + 0.12 * inch, 7.7 * inch, y + 0.12 * inch)
    c.setFont("Helvetica", 10)
    c.drawString(5.3 * inch, y, "Subtotal:")
    c.drawString(6.7 * inch, y, f"${subtotal:,.2f}")
    y -= 0.22 * inch
    c.drawString(5.3 * inch, y, "Tax (8.7% WA):")
    c.drawString(6.7 * inch, y, f"${tax:,.2f}")
    y -= 0.22 * inch
    c.setFont("Helvetica-Bold", 11)
    c.drawString(5.3 * inch, y, "Total:")
    c.drawString(6.7 * inch, y, f"${total:,.2f}")
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(0.75 * inch, 0.75 * inch,
                 "Thank you for your business. Payment due within 30 days.")


def _font(sz: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, sz)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_invoice_image() -> Image.Image:
    """Render the same content as a 200-DPI PNG so we can embed it
    into an image-only PDF (no text layer => forces OCR path)."""
    W, H = 1700, 2200
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    x0 = 130
    d.text((x0, 130), "INVOICE", fill="black", font=_font(60, bold=True))
    d.text((x0, 260), VENDOR, fill="black", font=_font(28, bold=True))
    for i, line in enumerate(VENDOR_ADDR):
        d.text((x0, 300 + i * 36), line, fill="black", font=_font(24))
    d.text((950, 260), "Bill to:", fill="black", font=_font(28, bold=True))
    d.text((950, 300), BILL_TO, fill="black", font=_font(24, bold=True))
    for i, line in enumerate(BILL_TO_ADDR):
        d.text((950, 340 + i * 36), line, fill="black", font=_font(24))
    d.text((x0, 480), f"Invoice Number: {NUMBER}", fill="black", font=_font(26, bold=True))
    d.text((x0, 520), f"Invoice Date:   {DATE}", fill="black", font=_font(26, bold=True))
    d.text((x0, 560), f"Due Date:       {DUE}", fill="black", font=_font(26, bold=True))
    d.text((x0, 600), "Currency:       USD", fill="black", font=_font(26, bold=True))
    y = 750
    d.text((x0,      y), "Description", fill="black", font=_font(26, bold=True))
    d.text((x0+830,  y), "Qty",         fill="black", font=_font(26, bold=True))
    d.text((x0+970,  y), "Unit Price",  fill="black", font=_font(26, bold=True))
    d.text((x0+1280, y), "Amount",      fill="black", font=_font(26, bold=True))
    d.line((x0, y + 44, x0 + 1450, y + 44), fill="black", width=2)
    subtotal = 0.0
    for desc, qty, unit in ITEMS:
        y += 70
        amount = qty * unit
        subtotal += amount
        d.text((x0,      y), desc,             fill="black", font=_font(24))
        d.text((x0+830,  y), str(qty),         fill="black", font=_font(24))
        d.text((x0+970,  y), f"${unit:,.2f}",  fill="black", font=_font(24))
        d.text((x0+1280, y), f"${amount:,.2f}", fill="black", font=_font(24))
    tax = round(subtotal * TAX_RATE, 2)
    total = round(subtotal + tax, 2)
    y += 130
    d.line((x0 + 970, y - 8, x0 + 1450, y - 8), fill="black", width=2)
    d.text((x0+970,  y), "Subtotal:",         fill="black", font=_font(24))
    d.text((x0+1280, y), f"${subtotal:,.2f}", fill="black", font=_font(24))
    y += 50
    d.text((x0+970,  y), "Tax (8.7% WA):", fill="black", font=_font(24))
    d.text((x0+1280, y), f"${tax:,.2f}",   fill="black", font=_font(24))
    y += 50
    d.text((x0+970,  y), "Total:",        fill="black", font=_font(28, bold=True))
    d.text((x0+1280, y), f"${total:,.2f}", fill="black", font=_font(28, bold=True))
    d.text((x0, H - 150),
           "Thank you for your business. Payment due within 30 days.",
           fill="black", font=_font(20))
    return img


# Variant 1: text-layer PDF (pdftotext-friendly)
text_pdf = OUT / "invoice-text.pdf"
c = canvas.Canvas(str(text_pdf), pagesize=LETTER)
draw_invoice_pdf(c)
c.showPage()
c.save()
print(f"wrote {text_pdf} ({text_pdf.stat().st_size:,} bytes)")

# Variant 2: image-only PDF (forces tesseract OCR)
img = draw_invoice_image()
scanned_pdf = OUT / "invoice-scanned.pdf"
img.save(scanned_pdf, "PDF", resolution=200)
print(f"wrote {scanned_pdf} ({scanned_pdf.stat().st_size:,} bytes)")

# Quick smoke: does pdftotext find anything in each?
import subprocess
for p in (text_pdf, scanned_pdf):
    try:
        out = subprocess.run(
            ["pdftotext", str(p), "-"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        has_text = "INVOICE" in out
        print(f"  {p.name}: pdftotext finds 'INVOICE' = {has_text}")
    except FileNotFoundError:
        print(f"  {p.name}: pdftotext not installed locally; OK to ship as-is")
        break
