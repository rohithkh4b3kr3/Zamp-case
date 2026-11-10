"""Generate realistic test invoices for the demo.

Each vendor gets its own layout and label vocabulary, so extraction has to cope
with real formatting variance. One invoice is rendered to an image and re-wrapped
as a PDF with no text layer, to simulate a scanned document.

Run:  .venv/Scripts/python scripts/generate_samples.py
"""
from __future__ import annotations

import io
import json
import random
from pathlib import Path

import pymupdf
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples"
OUT.mkdir(exist_ok=True)

BILL_TO = """<b>Lumen Retail Group Inc.</b><br>Accounts Payable<br>500 Market Street<br>San Francisco, CA 94105"""


def money(v: float) -> str:
    return f"{v:,.2f}"


def table(rows, headers, widths=None, cls="items"):
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = ""
    for r in rows:
        cells = []
        for i, c in enumerate(r):
            align = ' class="r"' if i >= len(r) - 3 and i > 0 else ""
            cells.append(f"<td{align}>{c}</td>")
        body += "<tr>" + "".join(cells) + "</tr>"
    return f'<table class="{cls}"><tr>{head}</tr>{body}</table>'


BASE_CSS = """
* { font-family: sans-serif; }
body { font-size: 10px; color: #222; }
h1 { font-size: 22px; margin: 0; }
h2 { font-size: 14px; margin: 0; }
.muted { color: #666; }
table { border-collapse: collapse; width: 100%; }
table.items th { text-align: left; border-bottom: 1px solid #333; padding: 4px; font-size: 9px; }
table.items td { padding: 5px 4px; border-bottom: 1px solid #ddd; }
.r { text-align: right; }
.box { border: 1px solid #ccc; padding: 6px; }
.totals td { padding: 3px 4px; }
.note { background: #fff4d6; padding: 6px; border: 1px solid #e0b84c; }
"""


def render(html: str, css: str = "", path: Path | None = None) -> pymupdf.Document:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)  # A4 points
    rect = pymupdf.Rect(40, 40, 555, 802)
    page.insert_htmlbox(rect, html, css=BASE_CSS + css)
    if path:
        doc.save(path)
    return doc


# --------------------------------------------------------------------------- #
# 1. Happy path — Northwind, clean text PDF, explicit PO, everything lines up
# --------------------------------------------------------------------------- #
def northwind_happy():
    lines = [("NW-CH-220", "Ergonomic mesh office chair", 40, 185.00),
             ("NW-PP-A4", "A4 copy paper, 80gsm (ream)", 100, 6.50)]
    sub = sum(q * p for _, _, q, p in lines)
    tax = round(sub * 0.08, 2)
    rows = [(s, d, q, money(p), money(q * p)) for s, d, q, p in lines]
    html = f"""
    <h1>Northwind Office Supplies Inc.</h1>
    <p class="muted">2200 Harrison St, Oakland, CA 94612 &middot; billing@northwind-supplies.example &middot; Tax ID: 94-3318275</p>
    <h2>INVOICE</h2>
    <table><tr><td width="50%">{BILL_TO}</td>
    <td><b>Invoice No:</b> INV-NW-10611<br><b>Invoice Date:</b> 2026-11-04<br><b>Due Date:</b> 2026-12-04<br>
    <b>PO Number:</b> PO-2026-0412<br><b>Terms:</b> Net 30</td></tr></table>
    <br>
    {table(rows, ["SKU", "Description", "Qty", "Unit Price", "Amount"])}
    <br>
    <table class="totals"><tr><td width="60%"></td><td>Subtotal</td><td class="r">{money(sub)}</td></tr>
    <tr><td></td><td>Sales Tax (8%)</td><td class="r">{money(tax)}</td></tr>
    <tr><td></td><td><b>Total Due (USD)</b></td><td class="r"><b>{money(sub + tax)}</b></td></tr></table>
    <br><div class="box"><b>Remit to:</b> Pacific Coast Bank &middot; Account Number: 000123454321 &middot; Routing: 121000358</div>
    <p class="muted">Thank you for your business.</p>
    """
    render(html, path=OUT / "01_happy_northwind.pdf")


# --------------------------------------------------------------------------- #
# 2. Scanned — Harbor Print, rendered to a noisy image, no text layer
# --------------------------------------------------------------------------- #
def harbor_scanned():
    lines = [("HP-BRO-A5", "Tri-fold brochure A5, full colour", 2000, 1.15),
             ("HP-BAN-6X3", "Vinyl banner 6x3 ft", 4, 145.00)]
    sub = sum(q * p for _, _, q, p in lines)
    tax = round(sub * 0.08, 2)
    rows = [(s, d, q, money(p), money(q * p)) for s, d, q, p in lines]
    html = f"""
    <h1>Harbor Print &amp; Signage</h1>
    <p>118 NW Front Ave, Portland, OR 97209 &middot; Tax ID: 91-6620394</p>
    <h2>TAX INVOICE</h2>
    <p><b>Invoice Number:</b> HPS-3318</p>
    <p><b>Date of Issue:</b> Nov 5, 2026</p>
    <p><b>Customer PO:</b> PO-2026-0461</p>
    <p><b>Bill To:</b> Lumen Retail Group Inc., 500 Market Street, San Francisco, CA</p>
    {table(rows, ["Item", "Description", "Qty", "Rate", "Line Total"])}
    <br>
    <table class="totals"><tr><td width="60%"></td><td>Subtotal</td><td class="r">{money(sub)}</td></tr>
    <tr><td></td><td>Sales Tax 8%</td><td class="r">{money(tax)}</td></tr>
    <tr><td></td><td><b>Amount Due</b></td><td class="r"><b>USD {money(sub + tax)}</b></td></tr></table>
    <p>Pay to: Pacific Coast Bank, Account No 000777120934, Routing 121000358</p>
    """
    doc = render(html)
    pix = doc[0].get_pixmap(dpi=150)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
    # scanner artefacts: slight skew, blur, speckle noise
    img = img.rotate(0.7, expand=False, fillcolor=255, resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    rnd = random.Random(7)
    px = img.load()
    w, h = img.size
    for _ in range(int(w * h * 0.002)):
        x, y = rnd.randrange(w), rnd.randrange(h)
        px[x, y] = rnd.choice([90, 140, 200])
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    scan = pymupdf.open()
    page = scan.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buf.getvalue())
    scan.save(OUT / "02_scanned_harbor.pdf")


# --------------------------------------------------------------------------- #
# 3. Edge: split billing that overruns the PO — Meridian
# --------------------------------------------------------------------------- #
def meridian_split():
    rows = [("1", "Full truckload freight Chicago to Dallas - shipment 3 of 3 (BOL 55120)", 1, money(4200), money(4200)),
            ("2", "Detention charges at consignee - 6 hrs", 1, money(1850), money(1850))]
    html = f"""
    <table><tr><td><h1>MERIDIAN LOGISTICS CO.</h1><p class="muted">900 W Fulton Market, Chicago, IL 60607<br>EIN 77-2056648</p></td>
    <td class="r"><h2>FREIGHT INVOICE</h2>Invoice #: ML-24-1043<br>Billing Date: 06 Nov 2026<br>Payment Due: 21 Nov 2026</td></tr></table>
    <br>
    <table><tr><td width="50%"><b>Customer</b><br>{BILL_TO}</td><td><b>Purchase Order:</b> PO-2026-0398<br><b>Lane:</b> CHI &rarr; DAL<br><b>Terms:</b> Net 15</td></tr></table>
    <br>
    {table(rows, ["#", "Service", "Qty", "Rate", "Charge"])}
    <br>
    <table class="totals"><tr><td width="60%"></td><td>Subtotal</td><td class="r">{money(6050)}</td></tr>
    <tr><td></td><td>Tax</td><td class="r">0.00</td></tr>
    <tr><td></td><td><b>TOTAL USD</b></td><td class="r"><b>{money(6050)}</b></td></tr></table>
    <br><p>Remit: Lakeshore Trust &middot; Acct 880021334590 &middot; ABA 071000013</p>
    """
    render(html, path=OUT / "03_edge_split_billing_meridian.pdf")


# --------------------------------------------------------------------------- #
# 4. Edge: no PO reference, price drift, partial goods receipt — Crestline
# --------------------------------------------------------------------------- #
def crestline_nopo():
    lines = [("CR-HF-0750", "3/4\" Stainless Hydraulic Fitting", 500, 12.55),
             ("CR-PG-0160", "Pressure Gauge, 0-160 PSI", 20, 89.00)]
    sub = round(sum(q * p for _, _, q, p in lines), 2)
    tax = round(sub * 0.08, 2)
    rows = [(d, s, q, money(p), money(q * p)) for s, d, q, p in lines]
    html = f"""
    <h1>Crestline Industrial Parts</h1>
    <p class="muted">4120 S Halsted St, Chicago, IL 60609 &middot; invoices@crestline-parts.example</p>
    <table><tr><td width="55%"><b>Sold To:</b><br>{BILL_TO}<br>Attn: Marcus Lee, Maintenance Dept.</td>
    <td><b>Invoice Number:</b> CIP-77815<br><b>Invoice Date:</b> November 7, 2026<br><b>Terms:</b> Net 45<br>
    <b>Ship Via:</b> Ground</td></tr></table>
    <br>
    {table(rows, ["Description", "Part No.", "Qty", "Unit Price", "Ext. Price"])}
    <br>
    <table class="totals"><tr><td width="60%"></td><td>Merchandise Total</td><td class="r">{money(sub)}</td></tr>
    <tr><td></td><td>Sales Tax 8.00%</td><td class="r">{money(tax)}</td></tr>
    <tr><td></td><td><b>Invoice Total</b></td><td class="r"><b>${money(sub + tax)}</b></td></tr></table>
    <br><p class="muted">Federal Tax ID 36-4471120. Remit to Midwest Commerce Bank, Account Number 551200784412, Routing 071923909.</p>
    """
    render(html, path=OUT / "04_edge_no_po_partial_receipt_crestline.pdf")


# --------------------------------------------------------------------------- #
# 5. Edge: disguised duplicate — Northwind re-sends an already-paid invoice
# --------------------------------------------------------------------------- #
def northwind_duplicate():
    html = f"""
    <h1>Northwind Office Supplies</h1>
    <p class="muted">2200 Harrison St, Oakland, CA 94612 &middot; Tax ID 94-3318275</p>
    <div class="note"><b>PAYMENT REMINDER</b> - our records show this invoice is outstanding. Please arrange payment at your earliest convenience.</div>
    <br>
    <table><tr><td width="50%">{BILL_TO}</td>
    <td><b>Invoice #:</b> NW10482<br><b>Date:</b> 09/28/2026<br><b>Your Ref:</b> PO 2026-0390</td></tr></table>
    <br>
    {table([("NW-DK-160", "Height-adjustable standing desk", 5, money(433.33), money(2166.65))], ["SKU", "Description", "Qty", "Unit Price", "Amount"])}
    <br>
    <table class="totals"><tr><td width="60%"></td><td>Subtotal</td><td class="r">2,166.65</td></tr>
    <tr><td></td><td>Sales Tax (8%)</td><td class="r">173.33</td></tr>
    <tr><td></td><td><b>Balance Due</b></td><td class="r"><b>$2,339.98</b></td></tr></table>
    <br><div class="box"><b>Remit to:</b> Pacific Coast Bank &middot; Account Number: 000123454321 &middot; Routing: 121000358</div>
    """
    render(html, path=OUT / "05_edge_duplicate_northwind.pdf")


# --------------------------------------------------------------------------- #
# 6. Edge: bank details changed on the invoice — Bluepeak (BEC fraud pattern)
# --------------------------------------------------------------------------- #
def bluepeak_bank_change():
    rows = [("BP-K8S-ENT", "Managed Kubernetes platform - Q4 2026 subscription", 1, money(15000), money(15000)),
            ("BP-SUP-PRM", "Premium 24x7 support - Q4 2026", 1, money(3000), money(3000))]
    html = f"""
    <h1>Bluepeak Cloud Services LLC</h1>
    <p class="muted">1201 Third Ave, Seattle, WA 98101 &middot; Tax ID: 82-4410937</p>
    <h2>Invoice</h2>
    <table><tr><td width="50%">{BILL_TO}</td>
    <td><b>Invoice Number:</b> BPC-2026-1107<br><b>Invoice Date:</b> 2026-11-02<br><b>Due Date:</b> 2026-11-16<br><b>PO Number:</b> PO-2026-0430</td></tr></table>
    <br>
    {table(rows, ["SKU", "Description", "Qty", "Unit Price", "Amount"])}
    <br>
    <table class="totals"><tr><td width="60%"></td><td>Subtotal</td><td class="r">18,000.00</td></tr>
    <tr><td></td><td>Sales Tax (8%)</td><td class="r">1,440.00</td></tr>
    <tr><td></td><td><b>Total Due</b></td><td class="r"><b>USD 19,440.00</b></td></tr></table>
    <br>
    <div class="note"><b>IMPORTANT - UPDATED BANKING DETAILS.</b> Please note our banking details have changed. Kindly update your records and remit all payments to the new account below. Payment is due within 14 days to avoid service interruption.</div>
    <br><div class="box">Bank Name: Everline Bank &middot; Account Number: 7730019925 &middot; Routing Number: 026009593</div>
    """
    render(html, path=OUT / "06_edge_bank_change_bluepeak.pdf")


# --------------------------------------------------------------------------- #
# 7. Extra: suspended vendor — Orion
# --------------------------------------------------------------------------- #
def orion_suspended():
    html = f"""
    <h1>Orion Facilities Management</h1>
    <p class="muted">77 Summer St, Boston, MA 02110 &middot; EIN 45-9902113</p>
    <h2>INVOICE</h2>
    <table><tr><td width="50%">{BILL_TO}</td>
    <td><b>Invoice No:</b> OFM-5521<br><b>Invoice Date:</b> 2026-11-01<br><b>PO Number:</b> PO-2026-0377</td></tr></table>
    <br>
    {table([("OR-JAN-MO", "Janitorial services - monthly (HQ) - October 2026", 1, money(3150), money(3150))], ["Code", "Description", "Qty", "Rate", "Amount"])}
    <br>
    <table class="totals"><tr><td width="60%"></td><td>Subtotal</td><td class="r">3,150.00</td></tr>
    <tr><td></td><td><b>Total Due</b></td><td class="r"><b>3,150.00</b></td></tr></table>
    <br><p>Remit to Harborview Savings, Account Number 330019920045, Routing 011000138</p>
    """
    render(html, path=OUT / "07_extra_suspended_vendor_orion.pdf")


# --------------------------------------------------------------------------- #
# 8. Extra: missing critical fields (no invoice number, no date) — Harbor
# --------------------------------------------------------------------------- #
def harbor_missing_fields():
    html = f"""
    <h1>Harbor Print &amp; Signage</h1>
    <p class="muted">118 NW Front Ave, Portland, OR 97209 &middot; studio@harborprint.example</p>
    <h2>Statement of charges</h2>
    <p><b>Bill To:</b> Lumen Retail Group Inc., 500 Market Street, San Francisco, CA</p>
    <p><b>Customer PO:</b> PO-2026-0461</p>
    {table([("HP-BAN-6X3", "Vinyl banner 6x3 ft - reprint", 2, money(145), money(290))], ["Item", "Description", "Qty", "Rate", "Line Total"])}
    <br>
    <table class="totals"><tr><td width="60%"></td><td>Subtotal</td><td class="r">290.00</td></tr>
    <tr><td></td><td>Sales Tax 8%</td><td class="r">23.20</td></tr>
    <tr><td></td><td><b>Amount Due</b></td><td class="r"><b>313.20</b></td></tr></table>
    <p>Pay to: Pacific Coast Bank, Account No 000777120934</p>
    """
    render(html, path=OUT / "08_extra_missing_fields_harbor.pdf")


# --------------------------------------------------------------------------- #
# 9. Extra: one PDF, two invoices (3 pages) - mailroom scans batches together
# --------------------------------------------------------------------------- #
def crestline_batch():
    head = """<h1>Crestline Industrial Parts</h1>
    <p class="muted">4120 S Halsted St, Chicago, IL 60609 &middot; Federal Tax ID 36-4471120</p>"""
    inv_a = f"""{head}
    <table><tr><td width="55%"><b>Sold To:</b><br>{BILL_TO}</td>
    <td><b>Invoice Number:</b> CIP-78002<br><b>Invoice Date:</b> November 8, 2026<br><b>PO Number:</b> PO-2026-0449<br><b>Terms:</b> Net 45</td></tr></table><br>
    {table([("Pressure relief safety valve 2 in.", "CR-SV-0200", 10, money(240), money(2400))], ["Description", "Part No.", "Qty", "Unit Price", "Ext. Price"])}
    <br><table class="totals"><tr><td width="60%"></td><td>Merchandise Total</td><td class="r">2,400.00</td></tr>
    <tr><td></td><td>Sales Tax 8.00%</td><td class="r">192.00</td></tr>
    <tr><td></td><td><b>Invoice Total</b></td><td class="r"><b>$2,592.00</b></td></tr></table>
    <br><p class="muted">Remit to Midwest Commerce Bank, Account Number 551200784412, Routing 071923909.</p>"""
    inv_b1 = f"""{head}
    <table><tr><td width="55%"><b>Sold To:</b><br>{BILL_TO}</td>
    <td><b>Invoice Number:</b> CIP-78003<br><b>Invoice Date:</b> November 8, 2026<br><b>PO Number:</b> PO-2026-0449<br><b>Terms:</b> Net 45</td></tr></table><br>
    {table([("Hydraulic hose assembly 10 ft", "CR-HS-1000", 25, money(64), money(1600))], ["Description", "Part No.", "Qty", "Unit Price", "Ext. Price"])}
    <p class="muted">Continued on next page</p>"""
    inv_b2 = """<p class="muted">Crestline Industrial Parts &middot; Invoice CIP-78003 &middot; Page 2 of 2</p><br>
    <table class="totals"><tr><td width="60%"></td><td>Merchandise Total</td><td class="r">1,600.00</td></tr>
    <tr><td></td><td>Sales Tax 8.00%</td><td class="r">128.00</td></tr>
    <tr><td></td><td><b>Invoice Total</b></td><td class="r"><b>$1,728.00</b></td></tr></table>
    <br><p class="muted">Remit to Midwest Commerce Bank, Account Number 551200784412, Routing 071923909.</p>"""
    doc = pymupdf.open()
    for html in (inv_a, inv_b1, inv_b2):
        page = doc.new_page(width=595, height=842)
        page.insert_htmlbox(pymupdf.Rect(40, 40, 555, 802), html, css=BASE_CSS)
    doc.save(OUT / "09_extra_multi_invoice_batch_crestline.pdf")


# --------------------------------------------------------------------------- #
# 10. Extra: foreign currency - German vendor, EUR, European number/date formats
# --------------------------------------------------------------------------- #
def atelier_nord_eur():
    def eur(v):  # 12.700,00
        return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    rows = [("AN-CON-FIX", "Design consultancy - retail fixture concept", 1, eur(9800), eur(9800)),
            ("AN-PRO-FIX", "Prototype display fixture", 2, eur(1450), eur(2900))]
    html = f"""
    <h1>Atelier Nord GmbH</h1>
    <p class="muted">Speicherstadt 12, 20457 Hamburg, Germany &middot; VAT ID: DE312456789</p>
    <h2>INVOICE</h2>
    <table><tr><td width="50%">{BILL_TO}<br>USA</td>
    <td><b>Invoice No.:</b> AN-2026-0318<br><b>Invoice Date:</b> 06.11.2026<br><b>Due Date:</b> 06.12.2026<br>
    <b>Your Ref:</b> PO-2026-0475</td></tr></table><br>
    {table(rows, ["Item", "Description", "Qty", "Unit (EUR)", "Amount (EUR)"])}
    <br><table class="totals"><tr><td width="55%"></td><td>Net amount</td><td class="r">12.700,00 €</td></tr>
    <tr><td></td><td>VAT 0% (reverse charge, export of services)</td><td class="r">0,00 €</td></tr>
    <tr><td></td><td><b>Total due (EUR)</b></td><td class="r"><b>12.700,00 €</b></td></tr></table>
    <br><div class="box">Bank: Nordsee Bank AG &middot; IBAN: DE89 3704 0044 0532 0130 00 &middot; BIC: NOSBDEHHXXX</div>
    """
    render(html, path=OUT / "10_extra_foreign_currency_eur.pdf")


# --------------------------------------------------------------------------- #
# 11/12. Learning: Harbor Print writes dates day-first (DD/MM/YYYY)
# --------------------------------------------------------------------------- #
def harbor_dmy(n: int, inv_no: str, raw_date: str, qty: int, fname: str):
    sub = round(qty * 0.60, 2)
    tax = round(sub * 0.08, 2)
    html = f"""
    <h1>Harbor Print &amp; Signage</h1>
    <p class="muted">118 NW Front Ave, Portland, OR 97209 &middot; Tax ID: 91-6620394</p>
    <h2>TAX INVOICE</h2>
    <p><b>Invoice Number:</b> {inv_no}</p>
    <p><b>Invoice Date:</b> {raw_date}</p>
    <p><b>Customer PO:</b> PO-2026-0470</p>
    <p><b>Bill To:</b> Lumen Retail Group Inc., 500 Market Street, San Francisco, CA</p>
    {table([("HP-POS-A3", "A3 poster, matte, full colour", qty, money(0.60), money(sub))], ["Item", "Description", "Qty", "Rate", "Line Total"])}
    <br><table class="totals"><tr><td width="60%"></td><td>Subtotal</td><td class="r">{money(sub)}</td></tr>
    <tr><td></td><td>Sales Tax 8%</td><td class="r">{money(tax)}</td></tr>
    <tr><td></td><td><b>Amount Due</b></td><td class="r"><b>USD {money(sub + tax)}</b></td></tr></table>
    <p>Pay to: Pacific Coast Bank, Account No 000777120934, Routing 121000358</p>
    """
    render(html, path=OUT / fname)


# --------------------------------------------------------------------------- #
# Sample emails (.eml) for the Inbox
# --------------------------------------------------------------------------- #
def make_emails():
    from email.message import EmailMessage
    from email.utils import format_datetime, make_msgid
    from datetime import datetime, timezone

    d = OUT / "emails"
    d.mkdir(exist_ok=True)

    def mail(fname, frm, subject, body, attachments, day):
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = frm, "ap@lumenretail.example", subject
        m["Date"] = format_datetime(datetime(2026, 11, day, 9, 14, tzinfo=timezone.utc))
        m["Message-ID"] = make_msgid(domain=frm.split("@")[-1].strip(">"))
        m.set_content(body)
        for name, data, mime in attachments:
            maintype, subtype = mime.split("/")
            m.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
        (d / fname).write_bytes(bytes(m))

    pdf = lambda f: (OUT / f).read_bytes()  # noqa: E731
    mail("01_northwind_invoice.eml", "Northwind Billing <billing@northwind-supplies.example>",
         "Invoice INV-NW-10611 for PO-2026-0412",
         "Hello,\n\nPlease find attached our invoice INV-NW-10611 for the October chair and paper order.\n\nBest,\nNorthwind Billing",
         [("INV-NW-10611.pdf", pdf("01_happy_northwind.pdf"), "application/pdf")], 4)
    mail("02_bluepeak_lookalike_domain.eml", "Bluepeak Accounts <accounts@bluepeak-billing.example>",
         "URGENT: Q4 invoice + updated bank details",
         "Hi AP team,\n\nPlease note our banking details have changed. Kindly process the attached invoice to the new account "
         "this week to avoid service interruption.\n\nThanks,\nBluepeak Accounts Receivable",
         [("BPC-2026-1107.pdf", pdf("06_edge_bank_change_bluepeak.pdf"), "application/pdf")], 3)
    mail("03_meridian_two_attachments.eml", "Meridian AR <ar@meridianlogistics.example>",
         "ML-24-1043 shipment 3 of 3 + detention log",
         "Hello,\n\nAttached is the invoice for the final Chicago-Dallas shipment and the detention log for the 6 hours at the consignee.\n\nRegards,\nMeridian AR",
         [("ML-24-1043.pdf", pdf("03_edge_split_billing_meridian.pdf"), "application/pdf"),
          ("detention_log.csv", b"date,arrived,released,hours\n2026-11-05,08:00,14:00,6\n", "text/csv")], 6)
    mail("04_atelier_nord_eur.eml", "Atelier Nord Buchhaltung <rechnung@ateliernord.example>",
         "Rechnung / Invoice AN-2026-0318",
         "Dear Sir or Madam,\n\nplease find attached our invoice AN-2026-0318 (EUR).\n\nKind regards,\nAtelier Nord GmbH",
         [("AN-2026-0318.pdf", pdf("10_extra_foreign_currency_eur.pdf"), "application/pdf")], 6)
    mail("05_no_attachment.eml", "Crestline Invoices <invoices@crestline-parts.example>",
         "Invoice CIP-77990",
         "Hi,\n\nPlease see the attached invoice for the October maintenance order.\n\nThanks,\nCrestline", [], 7)


# --------------------------------------------------------------------------- #
# 13-17. Invoice types: credit note, services (2-way), deposit + final, non-PO utility
# --------------------------------------------------------------------------- #
def _doc(vendor_html: str, title: str, meta: str, rows, headers, totals: list[tuple[str, float]], extra: str = "", fname: str = ""):
    tot = "".join(f"<tr><td width='60%'></td><td>{'<b>' + k + '</b>' if i == len(totals) - 1 else k}</td>"
                  f"<td class='r'>{'<b>' + money(v) + '</b>' if i == len(totals) - 1 else money(v)}</td></tr>"
                  for i, (k, v) in enumerate(totals))
    html = f"""{vendor_html}<h2>{title}</h2>
    <table><tr><td width="50%">{BILL_TO}</td><td>{meta}</td></tr></table><br>
    {table(rows, headers)}<br><table class="totals">{tot}</table><br>{extra}"""
    render(html, path=OUT / fname)


def northwind_credit_note():
    desk = 433.33
    tax = round(desk * 0.08, 2)
    _doc("""<h1>Northwind Office Supplies Inc.</h1><p class="muted">2200 Harrison St, Oakland, CA 94612 &middot; billing@northwind-supplies.example &middot; Tax ID: 94-3318275</p>""",
         "CREDIT NOTE",
         "<b>Credit Note No:</b> CN-NW-0045<br><b>Invoice Date:</b> 2026-11-06<br><b>Original invoice:</b> INV-NW-10482<br><b>PO Number:</b> PO-2026-0390",
         [("NW-DK-160", "Height-adjustable standing desk (returned, damaged in transit)", 1, money(desk), money(desk))],
         ["SKU", "Description", "Qty", "Unit Price", "Amount"],
         [("Subtotal", desk), ("Sales Tax (8%)", tax), ("Total Credit (USD)", desk + tax)],
         '<p class="muted">This credit will be applied to your account. No payment is due.</p>', "13_type_credit_note_northwind.pdf")


def brightline_services():
    lines = [("BA-RET-11", "Process advisory - November retainer", 1, 9600.00), ("BA-WKS-DAY", "Workshop facilitation (days)", 2, 1800.00)]
    sub = sum(q * p for _, _, q, p in lines)
    _doc("""<h1>Brightline Advisory LLC</h1><p class="muted">88 Pine St, New York, NY 10005 &middot; billing@brightline-advisory.example &middot; Tax ID: 27-3390651</p>""",
         "INVOICE",
         "<b>Invoice No:</b> BA-2026-114<br><b>Invoice Date:</b> 2026-11-06<br><b>PO Number:</b> PO-2026-0485<br><b>Terms:</b> 2/10 Net 30",
         [(s, d, q, money(p), money(q * p)) for s, d, q, p in lines], ["Code", "Service", "Qty", "Rate", "Amount"],
         [("Subtotal", sub), ("Tax (0%)", 0.0), ("Total Due (USD)", sub)],
         '<div class="box"><b>Remit to:</b> Hudson Valley Bank &middot; Account Number: 000610042318 &middot; Routing: 021000089</div>'
         '<p class="muted">2% discount if paid within 10 days; otherwise net 30.</p>', "14_type_services_discount_brightline.pdf")


KEYSTONE = """<h1>Keystone Fabrication Ltd</h1><p class="muted">1400 Smallman St, Pittsburgh, PA 15222 &middot; accounts@keystonefab.example &middot; Tax ID: 52-8810374</p>"""
KEYSTONE_BANK = '<div class="box"><b>Remit to:</b> Allegheny Trust &middot; Account Number: 000455190873 &middot; Routing: 043000096</div>'


def keystone_deposit():
    dep = 4500.00
    _doc(KEYSTONE, "DEPOSIT INVOICE",
         "<b>Invoice No:</b> KF-D-2207<br><b>Invoice Date:</b> 2026-10-14<br><b>PO Number:</b> PO-2026-0480<br><b>Terms:</b> Net 30",
         [("KF-DEP", "Advance payment: 30% deposit on custom steel display fixtures", 1, money(dep), money(dep))],
         ["Code", "Description", "Qty", "Unit Price", "Amount"],
         [("Subtotal", dep), ("Sales Tax (0%)", 0.0), ("Total Due (USD)", dep)],
         KEYSTONE_BANK + '<p class="muted">Deposit is deducted from the final invoice on delivery.</p>', "15_type_deposit_keystone.pdf")


def keystone_final():
    q, p = 12, 1250.00
    sub = q * p
    tax = round(sub * 0.08, 2)
    _doc(KEYSTONE, "INVOICE",
         "<b>Invoice No:</b> KF-2231<br><b>Invoice Date:</b> 2026-11-06<br><b>PO Number:</b> PO-2026-0480<br><b>Terms:</b> Net 30",
         [("KF-DSP-12", "Custom steel display fixture", q, money(p), money(sub))], ["SKU", "Description", "Qty", "Unit Price", "Amount"],
         [("Subtotal", sub), ("Sales Tax (8%)", tax), ("Total Due (USD)", sub + tax)],
         KEYSTONE_BANK + '<p class="muted">Final invoice on delivery. Deposit KF-D-2207 (4,500.00) previously invoiced.</p>', "16_type_final_after_deposit_keystone.pdf")


def metro_utility():
    sub = 1215.18
    tax = 97.22
    _doc("""<h1>Metro Power &amp; Light</h1><p class="muted">PO Box 7700, San Francisco, CA 94120 &middot; billing@metropower.example &middot; Tax ID: 36-4471902</p>""",
         "ELECTRICITY BILL",
         "<b>Invoice No:</b> MPL-2026-11<br><b>Invoice Date:</b> 2026-11-06<br><b>Account:</b> 4410-2207-55 (500 Market St)<br><b>Terms:</b> Net 15",
         [("ELEC", "Electricity supply, 15 Oct - 14 Nov", 1, money(sub), money(sub))], ["Code", "Description", "Qty", "Rate", "Amount"],
         [("Subtotal", sub), ("Utility Tax (8%)", tax), ("Total Due (USD)", sub + tax)],
         '<div class="box"><b>Remit to:</b> First Bay Bank &middot; Account Number: 000778812055 &middot; Routing: 121042882</div>', "17_type_non_po_utility_metro.pdf")


CATALOG = [
    {"file": "01_happy_northwind.pdf", "title": "Clean invoice, explicit PO", "kind": "happy",
     "expected": "APPROVED",
     "story": "Machine-readable PDF, PO referenced, prices/quantities match PO and goods receipt, math checks out."},
    {"file": "02_scanned_harbor.pdf", "title": "Scanned image invoice", "kind": "happy",
     "expected": "APPROVED",
     "story": "No text layer: the page is a skewed, noisy scan. The OCR path reads it, then the same checks run."},
    {"file": "03_edge_split_billing_meridian.pdf", "title": "Split billing overruns the PO", "kind": "edge",
     "expected": "NEEDS_REVIEW",
     "story": "Third invoice against a 3-shipment PO. The shipment itself is fine, but an unplanned detention charge pushes cumulative billing past the PO value."},
    {"file": "04_edge_no_po_partial_receipt_crestline.pdf", "title": "No PO reference + partial receipt", "kind": "edge",
     "expected": "NEEDS_REVIEW",
     "story": "Vendor omitted the PO. The process infers it from vendor, part numbers and amounts, then finds a small price drift (within tolerance) and 8 gauges billed that were never received."},
    {"file": "05_edge_duplicate_northwind.pdf", "title": "Disguised duplicate", "kind": "edge",
     "expected": "REJECTED",
     "story": "A 'payment reminder' re-sends an invoice already paid in September, with the number reformatted (NW10482 vs INV-NW-10482)."},
    {"file": "06_edge_bank_change_bluepeak.pdf", "title": "Bank details changed on invoice", "kind": "edge",
     "expected": "NEEDS_REVIEW",
     "story": "Everything matches the PO perfectly, but the remit-to account differs from the vendor master and the invoice pressures for urgent payment: a classic payment-fraud pattern."},
    {"file": "07_extra_suspended_vendor_orion.pdf", "title": "Suspended vendor", "kind": "extra",
     "expected": "REJECTED",
     "story": "Valid PO and correct amounts, but the vendor was suspended by Procurement."},
    {"file": "08_extra_missing_fields_harbor.pdf", "title": "Missing invoice number and date", "kind": "extra",
     "expected": "REJECTED",
     "story": "A 'statement of charges' with no invoice number or date cannot be posted; the process drafts a request for a corrected invoice."},
    {"file": "09_extra_multi_invoice_batch_crestline.pdf", "title": "Two invoices in one PDF", "kind": "extra",
     "expected": "APPROVED",
     "story": "A 3-page scan batch holding two invoices (the second runs over two pages). Pages are classified and split, and each invoice gets its own run and decision."},
    {"file": "10_extra_foreign_currency_eur.pdf", "title": "Foreign currency (EUR)", "kind": "extra",
     "expected": "APPROVED",
     "story": "German vendor billing in euros with European formats (12.700,00 €, 06.11.2026, IBAN). Converted to USD for the approval limit."},
    {"file": "11_learn_dates_harbor_first.pdf", "title": "Learning 1/2: day-first date", "kind": "learning",
     "expected": "NEEDS_REVIEW",
     "story": "Harbor writes 03/11/2026 meaning 3 Nov. Read US-style it looks 8 months old and is held. Correct the date once: the format is learned."},
    {"file": "12_learn_dates_harbor_second.pdf", "title": "Learning 2/2: rule applied", "kind": "learning",
     "expected": "APPROVED",
     "story": "Next Harbor invoice dated 07/11/2026. After the correction above, the learned rule reads it as 7 Nov automatically (not 11 July)."},
    {"file": "13_type_credit_note_northwind.pdf", "title": "Credit note", "kind": "extra",
     "expected": "NEEDS_REVIEW",
     "story": "Northwind credits one damaged desk from paid invoice INV-NW-10482. Matched to the original; AP applies it against payables, nothing is paid out."},
    {"file": "14_type_services_discount_brightline.pdf", "title": "Services invoice + early-pay discount", "kind": "extra",
     "expected": "APPROVED",
     "story": "Consulting retainer and workshop days: no goods receipt exists, so it is a 2-way match to the PO. Terms 2/10 Net 30, so payment is scheduled for the discount date."},
    {"file": "15_type_deposit_keystone.pdf", "title": "Deposit (prepayment) invoice", "kind": "extra",
     "expected": "APPROVED",
     "story": "A 30% advance on a fabrication PO, billed before delivery. Checked against the PO's agreed deposit instead of goods received."},
    {"file": "16_type_final_after_deposit_keystone.pdf", "title": "Final invoice after deposit", "kind": "extra",
     "expected": "APPROVED",
     "story": "Run after the deposit: the full PO value is billed on delivery, and the 4,500 deposit already paid is deducted from the amount to pay."},
    {"file": "17_type_non_po_utility_metro.pdf", "title": "Non-PO utility bill", "kind": "extra",
     "expected": "APPROVED",
     "story": "Electricity has no PO. The vendor master marks it non-PO: checked against the usual monthly amount and the non-PO spend limit instead."},
]


if __name__ == "__main__":
    northwind_happy()
    harbor_scanned()
    meridian_split()
    crestline_nopo()
    northwind_duplicate()
    bluepeak_bank_change()
    orion_suspended()
    harbor_missing_fields()
    crestline_batch()
    atelier_nord_eur()
    harbor_dmy(11, "HPS-3401", "03/11/2026", 500, "11_learn_dates_harbor_first.pdf")
    harbor_dmy(12, "HPS-3415", "07/11/2026", 400, "12_learn_dates_harbor_second.pdf")
    northwind_credit_note()
    brightline_services()
    keystone_deposit()
    keystone_final()
    metro_utility()
    make_emails()
    (OUT / "catalog.json").write_text(json.dumps(CATALOG, indent=2), encoding="utf-8")
    print("generated", len(CATALOG), "samples in", OUT)
