"""
Simple, pure-Python PDF 1.4 generator for HisabAI receipts and statements.
Zero external dependencies. Works on all platforms and Python 3.10+.
"""

import io
from typing import List, Tuple


class MinimalPDF:
    """
    Lightweight, robust PDF 1.4 writer in pure Python.
    Supports text, lines, rectangles, fill colors, and automatic xref table generation.
    """

    def __init__(self, width: float = 595.28, height: float = 841.89):  # Standard A4 in points
        self.width = width
        self.height = height
        self.objects: List[bytes] = []
        self.page_contents: List[str] = []
        self.current_y = height - 50

    def _add_object(self, data: bytes) -> int:
        self.objects.append(data)
        return len(self.objects)

    def draw_text(self, text: str, x: float, y: float, font_size: int = 12, bold: bool = False, color: Tuple[float, float, float] = (0, 0, 0)):
        # Sanitize text for standard PDF Latin-1 / ASCII font
        # Transliterate or clean non-ascii characters gracefully
        clean_text = text.encode("ascii", errors="replace").decode("ascii").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        font = "/F2" if bold else "/F1"
        r, g, b = color
        cmd = f"BT\n{r:.2f} {g:.2f} {b:.2f} rg\n{font} {font_size} Tf\n{x:.2f} {y:.2f} Td\n({clean_text}) Tj\nET\n"
        self.page_contents.append(cmd)

    def draw_line(self, x1: float, y1: float, x2: float, y2: float, width: float = 1, color: Tuple[float, float, float] = (0.7, 0.7, 0.7)):
        r, g, b = color
        cmd = f"{r:.2f} {g:.2f} {b:.2f} RG\n{width:.2f} w\n{x1:.2f} {y1:.2f} m\n{x2:.2f} {y2:.2f} l\nS\n"
        self.page_contents.append(cmd)

    def draw_rect(self, x: float, y: float, w: float, h: float, fill_color: Tuple[float, float, float] = None, stroke_color: Tuple[float, float, float] = None, stroke_width: float = 1):
        cmd = ""
        if fill_color:
            r, g, b = fill_color
            cmd += f"{r:.2f} {g:.2f} {b:.2f} rg\n"
        if stroke_color:
            r, g, b = stroke_color
            cmd += f"{r:.2f} {g:.2f} {b:.2f} RG\n{stroke_width:.2f} w\n"
        
        cmd += f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re\n"
        if fill_color and stroke_color:
            cmd += "B\n"
        elif fill_color:
            cmd += "f\n"
        elif stroke_color:
            cmd += "S\n"
        self.page_contents.append(cmd)

    def build(self) -> bytes:
        content_stream = "".join(self.page_contents).encode("latin-1")
        stream_len = len(content_stream)

        # We will assemble objects:
        # 1: Catalog
        # 2: Outlines
        # 3: Pages
        # 4: Page 1
        # 5: Font Helvetica
        # 6: Font Helvetica-Bold
        # 7: Content stream
        objects_data = [
            b"", # 1-indexed placeholder
            b"<< /Type /Catalog /Pages 3 0 R >>",
            b"<< /Type /Outlines /Count 0 >>",
            b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            f"<< /Type /Page /Parent 3 0 R /MediaBox [0 0 {self.width:.2f} {self.height:.2f}] /Contents 7 0 R /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>".encode("ascii"),
            b"<< /Type /Font /Subtype /Type1 /Name /F1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            b"<< /Type /Font /Subtype /Type1 /Name /F2 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
            f"<< /Length {stream_len} >>\nstream\n".encode("ascii") + content_stream + b"\nendstream"
        ]

        output = io.BytesIO()
        output.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        
        xref_offsets = []
        for i in range(1, len(objects_data)):
            xref_offsets.append(output.tell())
            output.write(f"{i} 0 obj\n".encode("ascii"))
            output.write(objects_data[i])
            output.write(b"\nendobj\n")

        startxref = output.tell()
        output.write(b"xref\n")
        output.write(f"0 {len(objects_data)}\n".encode("ascii"))
        output.write(b"0000000000 65535 f \n")
        for offset in xref_offsets:
            output.write(f"{offset:010d} 00000 n \n".encode("ascii"))

        output.write(b"trailer\n")
        output.write(f"<< /Size {len(objects_data)} /Root 1 0 R >>\n".encode("ascii"))
        output.write(b"startxref\n")
        output.write(f"{startxref}\n%%EOF\n".encode("ascii"))

        return output.getvalue()


def generate_receipt_pdf(
    shop_name: str,
    owner_name: str,
    customer_name: str,
    transaction_id: int,
    tx_type: str,
    amount_rupees: float,
    previous_balance: float,
    current_balance: float,
    timestamp_str: str,
    receipt_no: str = None
) -> bytes:
    """
    Generates a professional Kirana Khata payment / credit receipt in PDF format.
    Dimensions: 400x560 points (compact receipt format).
    """
    pdf = MinimalPDF(width=400, height=560)
    
    # Outer Border & Header card
    pdf.draw_rect(15, 15, 370, 530, fill_color=(0.98, 0.98, 0.99), stroke_color=(0.8, 0.85, 0.9), stroke_width=1.5)
    
    # Top banner
    is_jama = (tx_type.upper() == "JAMA")
    banner_color = (0.1, 0.45, 0.3) if is_jama else (0.6, 0.1, 0.15)
    pdf.draw_rect(15, 480, 370, 65, fill_color=banner_color)
    
    # Header text
    display_shop = shop_name or "HisabAI Digital Khata"
    pdf.draw_text(display_shop.upper(), 30, 520, font_size=16, bold=True, color=(1, 1, 1))
    sub_title = "BHUGTAN RASEED (PAYMENT RECEIPT)" if is_jama else "UDHAAR RASEED (CREDIT NOTE)"
    pdf.draw_text(sub_title, 30, 498, font_size=10, bold=False, color=(0.9, 0.95, 0.9))

    # Receipt Info bar
    r_no = receipt_no or f"HAB-{transaction_id:06d}"
    pdf.draw_text(f"Receipt No: {r_no}", 30, 455, font_size=10, bold=True, color=(0.2, 0.2, 0.3))
    pdf.draw_text(f"Date: {timestamp_str}", 220, 455, font_size=9, bold=False, color=(0.4, 0.4, 0.4))
    pdf.draw_line(30, 445, 370, 445, width=1, color=(0.85, 0.85, 0.85))

    # Customer Details box
    pdf.draw_rect(30, 385, 340, 50, fill_color=(1, 1, 1), stroke_color=(0.9, 0.9, 0.9))
    pdf.draw_text("CUSTOMER / GRAHAK:", 40, 420, font_size=8, bold=True, color=(0.5, 0.5, 0.5))
    pdf.draw_text(customer_name, 40, 400, font_size=14, bold=True, color=(0.1, 0.1, 0.1))

    # Amount Highlight Card
    amt_bg = (0.92, 0.98, 0.94) if is_jama else (0.99, 0.93, 0.93)
    amt_stroke = (0.2, 0.6, 0.3) if is_jama else (0.8, 0.3, 0.3)
    pdf.draw_rect(30, 275, 340, 95, fill_color=amt_bg, stroke_color=amt_stroke, stroke_width=1.5)
    
    label = "BHUGTAN RASHI (AMOUNT RECEIVED)" if is_jama else "UDHAAR RASHI (CREDIT GIVEN)"
    pdf.draw_text(label, 40, 345, font_size=9, bold=True, color=(0.3, 0.3, 0.3))
    pdf.draw_text(f"Rs. {amount_rupees:,.2f}", 40, 305, font_size=26, bold=True, color=banner_color)
    status_tag = "CLEARED / RECEIVED" if is_jama else "ADDED TO KHATA"
    pdf.draw_text(f"Status: {status_tag}", 40, 285, font_size=8, bold=False, color=(0.4, 0.4, 0.4))

    # Balance Summary Table
    pdf.draw_text("ACCOUNT BALANCE STATEMENT", 30, 245, font_size=10, bold=True, color=(0.2, 0.2, 0.2))
    pdf.draw_line(30, 238, 370, 238, width=1, color=(0.8, 0.8, 0.8))

    # Table rows
    pdf.draw_text("Previous Khata Balance:", 40, 218, font_size=10, bold=False, color=(0.3, 0.3, 0.3))
    pdf.draw_text(f"Rs. {previous_balance:,.2f}", 280, 218, font_size=10, bold=False, color=(0.2, 0.2, 0.2))
    pdf.draw_line(40, 210, 360, 210, width=0.5, color=(0.9, 0.9, 0.9))

    pdf.draw_text("Current Transaction:", 40, 192, font_size=10, bold=False, color=(0.3, 0.3, 0.3))
    sign = "-" if is_jama else "+"
    pdf.draw_text(f"{sign} Rs. {amount_rupees:,.2f}", 280, 192, font_size=10, bold=True, color=banner_color)
    pdf.draw_line(40, 184, 360, 184, width=0.5, color=(0.9, 0.9, 0.9))

    pdf.draw_text("Remaining Baki Balance:", 40, 165, font_size=11, bold=True, color=(0.1, 0.1, 0.1))
    bal_color = (0.7, 0.1, 0.1) if current_balance > 0 else (0.1, 0.5, 0.2)
    pdf.draw_text(f"Rs. {current_balance:,.2f}", 280, 165, font_size=12, bold=True, color=bal_color)
    pdf.draw_line(30, 153, 370, 153, width=1.2, color=(0.7, 0.7, 0.7))

    # Footer Notes
    pdf.draw_text("* Non-tax Kirana Digital Khata Record.", 30, 120, font_size=8, bold=False, color=(0.5, 0.5, 0.5))
    pdf.draw_text("Any discrepancy should be notified to the shopkeeper immediately.", 30, 108, font_size=8, bold=False, color=(0.5, 0.5, 0.5))
    
    if owner_name:
        pdf.draw_text(f"Issued by: {owner_name}", 30, 75, font_size=9, bold=True, color=(0.3, 0.3, 0.3))
    pdf.draw_text("Generated via HisabAI - Smart Khata for Indian Retailers", 30, 50, font_size=8, bold=False, color=(0.6, 0.6, 0.6))
    pdf.draw_text("www.hisabai.local", 290, 50, font_size=10, bold=False, color=(0.6, 0.6, 0.6))

    return pdf.build()


def generate_statement_pdf(
    shop_name: str,
    owner_name: str,
    customer_name: str,
    transactions: list,
    lifetime_udhaar: float,
    lifetime_jama: float,
    current_balance: float
) -> bytes:
    """
    Generates a full customer ledger statement in PDF format (A4).
    """
    pdf = MinimalPDF(width=595.28, height=841.89)

    # Header
    pdf.draw_rect(30, 740, 535, 75, fill_color=(0.1, 0.15, 0.25))
    display_shop = shop_name or "HisabAI Digital Khata"
    pdf.draw_text(display_shop.upper(), 50, 785, font_size=18, bold=True, color=(1, 1, 1))
    pdf.draw_text("CUSTOMER ACCOUNT LEDGER STATEMENT", 50, 760, font_size=11, bold=False, color=(0.8, 0.85, 0.95))

    # Customer & Shop info
    pdf.draw_text(f"Customer Name: {customer_name}", 50, 715, font_size=12, bold=True, color=(0.1, 0.1, 0.1))
    pdf.draw_text(f"Total Transactions: {len(transactions)}", 50, 698, font_size=10, bold=False, color=(0.4, 0.4, 0.4))
    if owner_name:
        pdf.draw_text(f"Shopkeeper: {owner_name}", 380, 715, font_size=10, bold=False, color=(0.3, 0.3, 0.3))

    # Summary Cards
    pdf.draw_rect(50, 625, 155, 55, fill_color=(0.98, 0.93, 0.93), stroke_color=(0.85, 0.7, 0.7))
    pdf.draw_text("TOTAL UDHAAR (CREDIT)", 60, 663, font_size=8, bold=True, color=(0.6, 0.2, 0.2))
    pdf.draw_text(f"Rs. {lifetime_udhaar:,.2f}", 60, 640, font_size=12, bold=True, color=(0.7, 0.1, 0.1))

    pdf.draw_rect(220, 625, 155, 55, fill_color=(0.93, 0.98, 0.94), stroke_color=(0.7, 0.85, 0.7))
    pdf.draw_text("TOTAL JAMA (PAID)", 230, 663, font_size=8, bold=True, color=(0.2, 0.5, 0.2))
    pdf.draw_text(f"Rs. {lifetime_jama:,.2f}", 230, 640, font_size=12, bold=True, color=(0.1, 0.5, 0.2))

    pdf.draw_rect(390, 625, 155, 55, fill_color=(0.95, 0.95, 0.98), stroke_color=(0.7, 0.7, 0.85))
    pdf.draw_text("NET OUTSTANDING BAKI", 400, 663, font_size=8, bold=True, color=(0.2, 0.2, 0.5))
    bal_col = (0.7, 0.1, 0.1) if current_balance > 0 else (0.1, 0.5, 0.2)
    pdf.draw_text(f"Rs. {current_balance:,.2f}", 400, 640, font_size=12, bold=True, color=bal_col)

    # Table Header
    pdf.draw_rect(50, 580, 495, 25, fill_color=(0.9, 0.92, 0.95))
    pdf.draw_text("Date & Time", 60, 588, font_size=9, bold=True, color=(0.2, 0.2, 0.3))
    pdf.draw_text("Type", 210, 588, font_size=9, bold=True, color=(0.2, 0.2, 0.3))
    pdf.draw_text("Amount", 320, 588, font_size=9, bold=True, color=(0.2, 0.2, 0.3))
    pdf.draw_text("Balance", 450, 588, font_size=9, bold=True, color=(0.2, 0.2, 0.3))

    # Table Rows
    y = 555
    running = 0.0
    for tx in transactions[-20:]:  # Last 20 for single-page statement
        amt = float(tx.get("amount", 0))
        txtype = tx.get("transaction_type", "UDHAAR")
        t_time = tx.get("timestamp", "")
        if isinstance(t_time, str) and len(t_time) > 16:
            t_time = t_time[:16]

        if txtype == "UDHAAR":
            running += amt
            type_label = "UDHAAR (Dr)"
            type_color = (0.7, 0.1, 0.1)
        else:
            running -= amt
            type_label = "JAMA (Cr)"
            type_color = (0.1, 0.5, 0.2)

        pdf.draw_text(str(t_time), 60, y, font_size=9, bold=False, color=(0.3, 0.3, 0.3))
        pdf.draw_text(type_label, 210, y, font_size=9, bold=True, color=type_color)
        pdf.draw_text(f"Rs. {amt:,.2f}", 320, y, font_size=9, bold=False, color=(0.1, 0.1, 0.1))
        pdf.draw_text(f"Rs. {running:,.2f}", 450, y, font_size=9, bold=True, color=(0.2, 0.2, 0.2))
        pdf.draw_line(50, y - 5, 545, y - 5, width=0.5, color=(0.92, 0.92, 0.92))
        y -= 22
        if y < 80:
            break

    # Footer
    pdf.draw_text("* Non-tax Kirana Digital Khata Record. Generated by HisabAI.", 50, 40, font_size=8, bold=False, color=(0.5, 0.5, 0.5))

    return pdf.build()
