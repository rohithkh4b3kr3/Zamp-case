"""Invoice field extraction.

Three readers, one output shape:

* ``text``  - PDF has a text layer: words + coordinates are rebuilt into visual rows
              and parsed with label / layout rules.
* ``ocr``   - scanned PDF (no text layer): pages are rasterised and read with RapidOCR
              (runs locally, no API), then the same row parser runs.
* ``llm``   - Claude reads the PDF directly (text or scanned) and returns a typed object.
              When a text layer also exists, the local parser runs too and the two are
              cross-checked, so a hallucinated total cannot slip through silently.

Every field carries a confidence score so later stages can decide whether a human
needs to look at the extraction itself.
"""
from __future__ import annotations

import base64
import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pymupdf

from . import config

TEXT_FLAGS = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES

# --------------------------------------------------------------------------- #
# Low level helpers
# --------------------------------------------------------------------------- #
# EU format (1.234,56 / 12,50) is tried first, then US (1,234.56 / 12.50)
MONEY_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+\.\d{2})(?![\d])")
NUM_TOKEN_RE = re.compile(r"^\d[\d.,]*$")
CURRENCY_TOKENS_RE = re.compile(r"\b(?:USD|EUR|GBP|INR|CAD)\b|[$€£₹]")
EU_NUMBER_RE = re.compile(r"^\d{1,3}(?:\.\d{3})+,\d{1,2}$|^\d+,\d{1,2}$")
SKU_RE = re.compile(r"\b(?!PO\b)(?!INV\b)[A-Z]{2,4}-[A-Z0-9]{2,5}(?:-[A-Z0-9]{1,5})?\b")


def to_number(s: str | None) -> float | None:
    if s is None:
        return None
    s = CURRENCY_TOKENS_RE.sub("", s).strip()
    if EU_NUMBER_RE.match(s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


CURRENCY_CODES = ("USD", "EUR", "GBP", "INR", "CAD")
CURRENCY_SYMBOLS = {"€": "EUR", "£": "GBP", "₹": "INR", "C$": "CAD", "CA$": "CAD"}


def detect_currency(text: str) -> str | None:
    """Explicit ISO codes win; otherwise the most frequent currency symbol ($ alone means USD)."""
    codes = re.findall(r"\b(USD|EUR|GBP|INR|CAD)\b", text)
    if codes:
        return max(set(codes), key=codes.count)
    counts = {cur: text.count(sym) for sym, cur in CURRENCY_SYMBOLS.items() if sym in text}
    if counts:
        return max(counts, key=counts.get)
    if "$" in text:
        return "USD"
    return None


MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


SLASH_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def is_ambiguous_date(raw: str | None) -> bool:
    """03/11/2026 could be March 11 or 3 November; 13/11/2026 cannot."""
    m = SLASH_DATE_RE.search(raw or "")
    return bool(m) and int(m[1]) <= 12 and int(m[2]) <= 12 and m[1] != m[2]


def parse_date(s: str | None, dayfirst: bool = False) -> str | None:
    """Return ISO date for the first recognisable date in ``s``.
    Slashed dates are read US-style (MM/DD) unless ``dayfirst`` or the first number is > 12."""
    if not s:
        return None
    s = s.strip()
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return _iso(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)  # European DD.MM.YYYY
    if m:
        return _iso(int(m[3]), int(m[2]), int(m[1]))
    m = SLASH_DATE_RE.search(s)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        if dayfirst or a > 12:
            return _iso(y, b, a)
        return _iso(y, a, b)
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", s)  # 06 Nov 2026
    if m and m[2][:3].lower() in MONTHS:
        return _iso(int(m[3]), MONTHS[m[2][:3].lower()], int(m[1]))
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)  # Nov 5, 2026
    if m and m[1][:3].lower() in MONTHS:
        return _iso(int(m[3]), MONTHS[m[1][:3].lower()], int(m[2]))
    return None


def _iso(y: int, mo: int, d: int) -> str | None:
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


_NOT_A_NAME = re.compile(
    r"[$€£₹¥*#@]|\d[\d,]*\.\d{2}\b|^\s*(for\s+a\s+total|total|sub\s*total|amount|balance|due|page|date|tel|phone|fax|e-?mail|www\.|https?:)",
    re.I,
)


def plausible_vendor_name(s: str | None) -> bool:
    """A company name, not a body line: no money/currency/masking characters, mostly letters, sensible length."""
    if not s:
        return False
    s = s.strip()
    if not 2 <= len(s) <= 80 or _NOT_A_NAME.search(s):
        return False
    letters = sum(ch.isalpha() for ch in s)
    digits = sum(ch.isdigit() for ch in s)
    return letters >= 2 and letters >= 0.5 * len(s.replace(" ", "")) and digits <= 4


def normalize_po(s: str | None) -> str | None:
    if not s:
        return None
    m = re.search(r"(\d{4})\s*[-/ ]\s*(\d{3,5})", s)
    if m:
        return f"PO-{m[1]}-{m[2].zfill(4)}"
    digits = re.sub(r"\D", "", s)
    return f"PO-{digits}" if digits else None


# --------------------------------------------------------------------------- #
# Document reading -> visual rows
# --------------------------------------------------------------------------- #
@dataclass
class Row:
    segments: list[str]
    y: float
    conf: float = 1.0
    page: int = 1
    # one normalised box (x0, y0, x1, y1 in 0..1 of the page) per segment
    boxes: list[tuple[float, float, float, float]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "  ".join(self.segments)

    def box(self, i: int | None = None, j: int | None = None) -> dict | None:
        """Region covering segment i (to j), or the whole row."""
        if not self.boxes:
            return None
        sel = self.boxes if i is None else self.boxes[i:(j if j is not None else i) + 1]
        return {"page": self.page, "x0": round(float(min(b[0] for b in sel)), 4), "y0": round(float(min(b[1] for b in sel)), 4),
                "x1": round(float(max(b[2] for b in sel)), 4), "y1": round(float(max(b[3] for b in sel)), 4)}

    def box_of(self, text: str) -> dict | None:
        """Box of the segment(s) holding `text`; falls back to the whole row."""
        t = re.sub(r"\s+", "", text)
        for i, s in enumerate(self.segments):
            if t and t in re.sub(r"\s+", "", s):
                return self.box(i)
        return self.box()


@dataclass
class Doc:
    page_count: int
    has_text_layer: bool
    rows: list[Row]
    full_text: str
    title_candidates: list[str] = field(default_factory=list)
    ocr_confidence: float | None = None


def inspect_pdf(path: str) -> dict[str, Any]:
    pdf = pymupdf.open(path)
    chars = sum(len(p.get_text(flags=TEXT_FLAGS).strip()) for p in pdf)
    images = sum(len(p.get_images()) for p in pdf)
    return {"page_count": pdf.page_count, "text_chars": chars, "images": images,
            "has_text_layer": chars > 40, "encrypted": pdf.is_encrypted}


def read_text_layer(path: str) -> Doc:
    pdf = pymupdf.open(path)
    rows: list[Row] = []
    titles: list[tuple[float, str]] = []
    full = []
    y_offset = 0.0
    for pno, page in enumerate(pdf, start=1):
        full.append(page.get_text(flags=TEXT_FLAGS))
        words = page.get_text("words", flags=TEXT_FLAGS)
        W, H = page.rect.width, page.rect.height
        rows.extend(_group_rows([(w[0], w[1], w[2], w[3], w[4], 1.0, (w[0] / W, w[1] / H, w[2] / W, w[3] / H))
                                 for w in words], y_offset, gap=7.0, page=pno))
        for block in page.get_text("dict", flags=TEXT_FLAGS)["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    t = span["text"].strip()
                    if len(t) > 3:
                        titles.append((span["size"], t))
        y_offset += page.rect.height
    titles.sort(key=lambda t: -t[0])
    return Doc(pdf.page_count, True, rows, "\n".join(full), [t for _, t in titles[:6]])


_ocr_engine = None


def _ocr():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr import RapidOCR  # heavy import, only when a scan shows up
        _ocr_engine = RapidOCR()
    return _ocr_engine


def google_vision_available() -> bool:
    if not config.GOOGLE_APPLICATION_CREDENTIALS:
        return False
    try:
        import google.cloud.vision  # noqa: F401
        return True
    except ImportError:
        return False


def _google_vision_ocr(png: bytes):
    """Same engine ai24c uses for scans: Google Cloud Vision document_text_detection (word level)."""
    from google.cloud import vision

    client = vision.ImageAnnotatorClient()
    resp = client.document_text_detection(image=vision.Image(content=png))
    if resp.error.message:
        raise RuntimeError(resp.error.message)
    boxes, txts, confs = [], [], []
    for page in resp.full_text_annotation.pages:
        for block in page.blocks:
            for para in block.paragraphs:
                for word in para.words:
                    v = word.bounding_box.vertices
                    boxes.append([(v[0].x, v[0].y), (v[1].x, v[1].y), (v[2].x, v[2].y), (v[3].x, v[3].y)])
                    txts.append("".join(s.text for s in word.symbols))
                    confs.append(word.confidence or 0.9)
    return boxes, txts, confs


def ocr_engine_name() -> str:
    return "google-vision" if google_vision_available() else "rapidocr"


def read_with_ocr(path: str, log=lambda m: None) -> Doc:
    import numpy as np

    pdf = pymupdf.open(path)
    rows: list[Row] = []
    titles: list[tuple[float, str]] = []
    scores: list[float] = []
    y_offset = 0.0
    use_google = google_vision_available()
    for pno, page in enumerate(pdf, start=1):
        pix = page.get_pixmap(dpi=200)
        boxes = None
        if use_google:
            try:
                boxes, txts, confs = _google_vision_ocr(pix.tobytes("png"))
                # Google returns words; group them per line like RapidOCR's line boxes
            except Exception as e:  # credentials / quota / network -> local engine
                log(f"Google Vision failed ({type(e).__name__}); using local RapidOCR")
                use_google = False
        if boxes is None:
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
            if pix.n == 4:
                img = img[:, :, :3]
            res = _ocr()(img)
            if res is None or res.boxes is None:
                continue
            boxes, txts, confs = [[tuple(p) for p in b] for b in res.boxes], list(res.txts), list(res.scores)
        # deskew: median angle of the wider boxes' top edges
        angles = [math.atan2(b[1][1] - b[0][1], b[1][0] - b[0][0]) for b in boxes if (b[1][0] - b[0][0]) > 120]
        a = statistics.median(angles) if angles else 0.0
        items = []
        for b, t, s in zip(boxes, txts, confs):
            cx = sum(p[0] for p in b) / 4
            cy = sum(p[1] for p in b) / 4
            ry = -cx * math.sin(a) + cy * math.cos(a)
            rx = cx * math.cos(a) + cy * math.sin(a)
            h = abs(b[3][1] - b[0][1])
            w = abs(b[1][0] - b[0][0])
            xs, ys = [p[0] for p in b], [p[1] for p in b]
            nbox = (min(xs) / pix.w, min(ys) / pix.h, max(xs) / pix.w, max(ys) / pix.h)
            items.append((rx - w / 2, ry - h / 2, rx + w / 2, ry + h / 2, t, float(s), nbox))
            scores.append(float(s))
            if cy < pix.h * 0.2:
                titles.append((h, t))
        # Google gives words (merge into cells by gap); RapidOCR already gives cell-level boxes
        rows.extend(_group_rows(items, y_offset, gap=(18.0 if use_google else None),
                                segment_per_item=not use_google, page=pno))
        y_offset += pix.h
    titles.sort(key=lambda t: -t[0])
    full = "\n".join(r.text for r in rows)
    return Doc(pdf.page_count, False, rows, full,
               [t for _, t in titles[:6]], round(statistics.mean(scores), 3) if scores else 0.0)


def _group_rows(items, y_offset: float, gap: float | None, segment_per_item: bool = False, page: int = 1) -> list[Row]:
    """Group positioned words into visual rows, then into cell segments."""
    if not items:
        return []
    items = sorted(items, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    heights = [w[3] - w[1] for w in items]
    tol = max(2.5, statistics.median(heights) * 0.55)
    groups: list[list] = []
    for w in items:
        cy = (w[1] + w[3]) / 2
        if groups and abs(groups[-1][0] - cy) <= tol:
            groups[-1][1].append(w)
            n = len(groups[-1][1])
            groups[-1][0] = (groups[-1][0] * (n - 1) + cy) / n
        else:
            groups.append([cy, [w]])
    rows = []
    for cy, ws in groups:
        ws.sort(key=lambda w: w[0])
        segs: list[str] = []
        sboxes: list[list[float]] = []
        confs = [w[5] for w in ws]
        last_x1 = None
        for w in ws:
            nb = w[6]
            if segment_per_item or last_x1 is None or (gap is not None and w[0] - last_x1 > gap):
                segs.append(w[4])
                sboxes.append(list(nb))
            else:
                segs[-1] += " " + w[4]
                b = sboxes[-1]
                sboxes[-1] = [min(b[0], nb[0]), min(b[1], nb[1]), max(b[2], nb[2]), max(b[3], nb[3])]
            last_x1 = w[2]
        keep = [(s.strip(), tuple(b)) for s, b in zip(segs, sboxes) if s.strip()]
        rows.append(Row([s for s, _ in keep], cy + y_offset, min(confs), page, [b for _, b in keep]))
    return rows


# --------------------------------------------------------------------------- #
# Row parser (shared by text + OCR readers)
# --------------------------------------------------------------------------- #
LABELS = {
    "invoice_number": re.compile(r"\b(?:invoice|inv|bill|credit\s*(?:note|memo))\s*(?:no\.?|number|num|#)\s*[:#.]?\s*#?\s*([A-Z0-9][A-Z0-9\-/_.]{2,})", re.I),
    "invoice_date": re.compile(r"\b(?:invoice\s*date|date\s*of\s*issue|billing\s*date|issue\s*date|invoice\s*dt)\s*[:.]?\s*(.+)$", re.I),
    "plain_date": re.compile(r"^\s*date\s*[:.]\s*(.+)$", re.I),
    "due_date": re.compile(r"\b(?:due\s*date|payment\s*due|date\s*due)\s*[:.]?\s*(.+)$", re.I),
    "po": re.compile(r"\bP\.?\s?O\.?\s*(?:number|no\.?|#)?\s*[:#-]?\s*(?:PO)?[\s-]*(\d{4}\s*[-/ ]\s*\d{3,5})", re.I),
    "po_label": re.compile(r"\b(?:purchase\s*order|your\s*ref(?:erence)?|customer\s*po|order\s*ref)\s*(?:no\.?|number|#)?\s*[:#.]?\s*(?:P\.?O\.?)?[\s-]*(\d{4}\s*[-/ ]\s*\d{3,5})", re.I),
    "tax_id": re.compile(r"\b(?:federal\s*tax\s*id|tax\s*id|ein|tin|ust-?id(?:nr)?\.?|vat\s*(?:id|no\.?|number|reg(?:istration)?\.?\s*(?:no\.?)?)?)\s*[:#.]?\s*(\d{2}-\d{7}|[A-Z]{2}\s?\d{8,12})", re.I),
    "iban": re.compile(r"\bIBAN\s*[:.]?\s*([A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,3})?)", re.I),
    "account": re.compile(r"\b(?:account\s*(?:number|no\.?|#)|acct\.?\s*(?:no\.?|#)?)\s*[:#.]?\s*(\d[\d\s-]{5,22}\d)", re.I),
    "routing": re.compile(r"\b(?:routing(?:\s*(?:number|no\.?))?|aba)\s*[:#.]?\s*(\d{9})\b", re.I),
    "bank_name": re.compile(r"\b(?:bank\s*name\s*[:.]?\s*|remit(?:\s*to)?\s*[:.]?\s*|pay\s*to\s*[:.]?\s*)?([A-Z][A-Za-z&.'\- ]{1,40}?\s(?:Bank|Trust|Savings|Credit Union))\b"),
}
SUBTOTAL_RE = re.compile(r"\b(sub\s*-?\s*total|merchandise\s*total|net\s*amount|total\s*before\s*tax|total\s*excl)", re.I)
TAX_RE = re.compile(r"\b(sales\s*tax|tax|vat|gst)\b(?!\s*(?:id|invoice))", re.I)
TOTAL_RE = re.compile(r"\b(total\s*due|amount\s*due|balance\s*due|grand\s*total|invoice\s*total|total\s*usd|total\s*\(usd\)|total\s*payable|^total\b)", re.I)
INCL_TAX_RE = re.compile(r"incl(?:\.|uding|udes)?\s*(?:sales\s*)?tax|tax\s*included", re.I)
HEADER_RE = re.compile(r"\b(qty|quantity|units?)\b", re.I)

CUES = {
    "bank_change": re.compile(r"(bank(?:ing)?\s*details\s*(?:have\s*)?changed|new\s*(?:bank|account)\s*details|updated\s*banking|change\s*of\s*bank|remit\s*all\s*payments\s*to\s*the\s*new)", re.I),
    "urgency": re.compile(r"(urgent|immediately|avoid\s*service\s*interruption|within\s*24\s*hours|final\s*notice)", re.I),
    "reminder": re.compile(r"(payment\s*reminder|reminder|outstanding|overdue|past\s*due|copy\s*of\s*invoice|re-?sent|duplicate\s*copy)", re.I),
    "statement": re.compile(r"(statement\s*of\s*(?:account|charges)|pro\s*-?\s*forma|quotation|estimate)", re.I),
    # invoice types the pipeline treats differently (see pipeline.classify)
    "credit_note": re.compile(r"(credit\s*(?:note|memo)|gutschrift)", re.I),
    "prepayment": re.compile(r"(deposit\s*invoice|advance\s*payment|pre-?payment|down\s*payment|milestone\s*(?:billing|payment)|progress\s*(?:billing|payment|claim))", re.I),
    "discount_terms": re.compile(r"(\d+(?:\.\d+)?\s*%?\s*/\s*\d+\s*,?\s*net\s*\d+|\d+(?:\.\d+)?\s*%\s*(?:discount\s*)?(?:if\s*paid\s*)?(?:within|in)\s*\d+\s*days)", re.I),
}


def _row_money(row: Row) -> list[float]:
    return [v for v in (to_number(m) for m in MONEY_RE.findall(row.text)) if v is not None]


def _fit(q: float, u: float, a: float) -> bool:
    return q > 0 and u >= 0 and abs(q * u - a) <= max(0.02, a * 0.001)


def _split_merged(tok: str, amount: float) -> tuple[float, float] | None:
    """OCR often glues qty and unit price ("20001.15"). Find the split that satisfies qty*unit=amount."""
    t = tok.replace(",", "").replace("$", "")
    if "." not in t:
        return None
    int_part, dec = t.split(".", 1)
    for i in range(1, len(int_part)):
        q, u = to_number(int_part[:i]), to_number(int_part[i:] + "." + dec)
        if q is not None and u is not None and _fit(q, u, amount):
            return q, u
    return None


def _parse_line_item(row: Row) -> dict | None:
    segs = row.segments
    tail: list[str] = []
    i = len(segs) - 1
    while i >= 0:
        toks = CURRENCY_TOKENS_RE.sub(" ", segs[i]).split()
        if toks and all(NUM_TOKEN_RE.match(t) for t in toks):
            tail = toks + tail
            i -= 1
        else:
            break
    head = segs[: i + 1]
    if not head or len(tail) < 2:
        return None
    nums = [to_number(t) for t in tail]
    qty = unit = amount = None
    if len(nums) >= 3 and _fit(nums[-3], nums[-2], nums[-1]):
        qty, unit, amount = nums[-3], nums[-2], nums[-1]
    elif len(nums) >= 2:
        sp = _split_merged(tail[-2], nums[-1])
        if sp:
            (qty, unit), amount = sp, nums[-1]
        elif len(nums) == 2 and _fit(1, nums[-2], nums[-1]) and nums[-2] == nums[-1]:
            qty, unit, amount = 1.0, nums[-2], nums[-1]
    if amount is None:
        return None
    text = " ".join(head)
    sku_m = SKU_RE.search(text)
    sku = sku_m.group(0) if sku_m else None
    desc = text.replace(sku, "") if sku else text
    desc = re.sub(r"^\s*\d{1,2}\s+(?=[A-Za-z])", "", desc)
    desc = re.sub(r"\s{2,}", " ", desc).strip(" -|·")
    if not desc or TOTAL_RE.search(desc) or SUBTOTAL_RE.search(desc):
        return None
    return {"sku": sku, "description": desc, "qty": qty, "unit_price": unit, "amount": amount}


def parse_rows(doc: Doc) -> dict[str, Any]:
    out: dict[str, Any] = {
        "vendor_name": None, "vendor_tax_id": None, "invoice_number": None, "invoice_date": None,
        "due_date": None, "po_number": None, "currency": None, "subtotal": None, "tax_amount": None,
        "tax_rate": None, "tax_inclusive": False, "total": None,
        "bank": {"bank_name": None, "account_number": None, "routing_number": None},
        "line_items": [], "cues": [], "raw_text_excerpt": doc.full_text[:4000],
    }
    conf: dict[str, float] = {}
    boxes: dict[str, dict] = {}
    base = 0.97 if doc.has_text_layer else min(0.97, (doc.ocr_confidence or 0.8))

    def setf(key, value, c, box=None):
        if value in (None, "") or out.get(key) not in (None, ""):
            return
        out[key] = value
        conf[key] = round(c, 3)
        if box:
            boxes[key] = box

    in_items = False
    for row in doc.rows:
        t = row.text
        rc = base * (row.conf if not doc.has_text_layer else 1.0)
        for seg_i, seg in enumerate(row.segments):
            nxt = row.segments[seg_i + 1] if seg_i + 1 < len(row.segments) else ""
            pair = row.box(seg_i, seg_i + 1) if nxt else row.box(seg_i)
            m = LABELS["invoice_number"].search(seg)
            if m and re.search(r"\d", m[1]):
                setf("invoice_number", m[1].strip(".-"), rc, row.box(seg_i))
            else:
                m = LABELS["invoice_number"].search(seg + " " + nxt)
                # only when the label itself sits in this cell and the value in the next one
                if m and m.start() < len(seg) and re.search(r"\d", m[1]):
                    setf("invoice_number", m[1].strip(".-"), rc, pair)
            m = LABELS["invoice_date"].search(seg)
            if m:
                own = parse_date(m[1])
                setf("invoice_date", own or parse_date(nxt), rc, row.box(seg_i) if own else pair)
                if out.get("invoice_date") and not out.get("invoice_date_raw"):
                    out["invoice_date_raw"] = (m[1] if own else nxt).strip()
            m = LABELS["due_date"].search(seg)
            if m:
                own = parse_date(m[1])
                setf("due_date", own or parse_date(nxt), rc, row.box(seg_i) if own else pair)
                if out.get("due_date") and not out.get("due_date_raw"):
                    out["due_date_raw"] = (m[1] if own else nxt).strip()
            m = LABELS["plain_date"].search(seg)
            if m and not out["invoice_date"]:
                setf("invoice_date", parse_date(m[1]), rc * 0.9, row.box(seg_i))
            m = LABELS["po_label"].search(seg) or LABELS["po"].search(seg)
            if m:
                setf("po_number", normalize_po(m[1]), rc, row.box(seg_i))
        m = LABELS["tax_id"].search(t)
        if m:
            setf("vendor_tax_id", m[1], rc, row.box_of(m[1]))
        m = LABELS["account"].search(t)
        if m and not out["bank"]["account_number"]:
            out["bank"]["account_number"] = re.sub(r"\D", "", m[1])
            conf["bank.account_number"] = round(rc, 3)
            boxes["bank"] = row.box_of(m[1].strip())
        m = LABELS["routing"].search(t)
        if m and not out["bank"]["routing_number"]:
            out["bank"]["routing_number"] = m[1]
        mi = LABELS["iban"].search(t)
        if mi and not out["bank"]["account_number"]:
            out["bank"]["account_number"] = re.sub(r"\s", "", mi[1]).upper()
            conf["bank.account_number"] = round(rc, 3)
            boxes["bank"] = row.box_of(mi[1].strip())
        if m or mi or LABELS["account"].search(t):
            bm = LABELS["bank_name"].search(t)
            if bm and not out["bank"]["bank_name"]:
                out["bank"]["bank_name"] = bm[1].strip()

        if out["currency"] is None and re.search(r"\bUSD\b|\$", t):
            out["currency"] = "USD"
        if INCL_TAX_RE.search(t):
            out["tax_inclusive"] = True

        if HEADER_RE.search(t) and re.search(r"(price|rate|amount|total|charge)", t, re.I):
            in_items = True
            continue
        money = _row_money(row)
        is_total_row = bool(TOTAL_RE.search(t)) and not SUBTOTAL_RE.search(t)
        if SUBTOTAL_RE.search(t) and money:
            setf("subtotal", money[-1], rc, row.box())
            in_items = False
            continue
        if TAX_RE.search(t) and not is_total_row and not LABELS["tax_id"].search(t) and "invoice" not in t.lower():
            rm = re.search(r"(\d+(?:\.\d+)?)\s*%", t)
            if rm:
                setf("tax_rate", float(rm[1]) / 100, rc, row.box())
            vals = [v for v in money if not (rm and abs(v - float(rm[1])) < 1e-9)]
            if vals:
                setf("tax_amount", vals[-1], rc, row.box())
            elif re.search(r"\b0\.00\b", t):
                setf("tax_amount", 0.0, rc, row.box())
            in_items = False
            continue
        if is_total_row and money:
            out["total"] = max(money)  # last total-like row wins
            conf["total"] = round(rc, 3)
            boxes["total"] = row.box()
            in_items = False
            continue
        if in_items:
            item = _parse_line_item(row)
            if item:
                item["confidence"] = round(rc, 3)
                item["box"] = row.box()
                out["line_items"].append(item)

    # Values that wrapped onto the next visual line: retry on the flattened text.
    flat = re.sub(r"\s+", " ", doc.full_text)
    if not out["vendor_tax_id"]:
        m = LABELS["tax_id"].search(flat)
        if m:
            setf("vendor_tax_id", m[1], base * 0.9)
    if not out["bank"]["account_number"]:
        m = LABELS["account"].search(flat)
        if m:
            out["bank"]["account_number"] = re.sub(r"\D", "", m[1])
            conf["bank.account_number"] = round(base * 0.9, 3)
    if not out["bank"]["routing_number"]:
        m = LABELS["routing"].search(flat)
        if m:
            out["bank"]["routing_number"] = m[1]
    if out["bank"]["bank_name"]:
        out["bank"]["bank_name"] = re.sub(r"^(?:remit(?:\s*to)?|pay\s*to|bank\s*name)\s*[:.]?\s*", "",
                                          out["bank"]["bank_name"], flags=re.I).strip()

    # Vendor name: biggest type near the top that is not a document title.
    for cand in doc.title_candidates:
        if not re.search(r"\b(invoice|statement|bill to|tax invoice)\b", cand, re.I) and plausible_vendor_name(cand):
            setf("vendor_name", cand.strip(), base * 0.95)
            break

    # Derived values (lower confidence, but better than nothing)
    if out["subtotal"] is None and out["line_items"]:
        setf("subtotal", round(sum(i["amount"] for i in out["line_items"]), 2), 0.7)
    if out["total"] is None and out["subtotal"] is not None and out["tax_amount"] is not None:
        setf("total", round(out["subtotal"] + out["tax_amount"], 2), 0.6)
    if out["tax_amount"] is None and out["subtotal"] is not None and out["total"] is not None:
        diff = round(out["total"] - out["subtotal"], 2)
        if diff >= 0:
            setf("tax_amount", diff, 0.7)

    out["currency"] = detect_currency(doc.full_text)
    if out.get("vendor_tax_id"):
        out["vendor_tax_id"] = out["vendor_tax_id"].replace(" ", "")
    if out.get("invoice_date_raw") and is_ambiguous_date(out["invoice_date_raw"]):
        out["date_ambiguous"] = True

    for k, rx in CUES.items():
        mm = rx.search(doc.full_text)
        if mm:
            cue = {"type": k, "evidence": mm.group(0)}
            b = locate(doc, mm.group(0).split("\n")[0])
            if b:
                cue["box"] = b
            out["cues"].append(cue)

    for key in ("vendor_name", "vendor_tax_id"):
        if out.get(key) and key not in boxes:
            b = locate(doc, out[key])
            if b:
                boxes[key] = b
    out["field_confidence"] = conf
    out["field_boxes"] = boxes
    return out


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9.]", "", str(s).lower())


def locate(doc: Doc, value: Any) -> dict | None:
    """Find where a value is printed: the first row segment that contains it (spacing/case-insensitive)."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        needles = [f"{value:,.2f}", f"{value:.2f}"]
    else:
        needles = [str(value)]
    needles = [_squash(n) for n in needles if _squash(n)]
    if not needles:
        return None
    for row in doc.rows:
        for i, seg in enumerate(row.segments):
            s = _squash(seg)
            if any(n in s for n in needles):
                return row.box(i)
        if any(n in _squash(row.text) for n in needles):
            return row.box()
    return None


def boxes_for(doc: Doc, inv: dict) -> dict:
    """Locate every extracted value on the page. Used when a model (not the row parser) produced the values."""
    out = {}
    for key in ("vendor_name", "vendor_tax_id", "invoice_number", "po_number", "subtotal", "tax_amount", "total"):
        b = locate(doc, inv.get(key))
        if b:
            out[key] = b
    if inv.get("po_number"):
        digits = re.search(r"(\d{4})-(\d{3,5})", inv["po_number"])
        if digits and "po_number" not in out:
            out["po_number"] = locate(doc, f"{digits[1]}-{digits[2]}") or locate(doc, f"{digits[1]}-{int(digits[2])}")
    acct = (inv.get("bank") or {}).get("account_number")
    if acct:
        b = locate(doc, acct)
        if b:
            out["bank"] = b
    for i, li in enumerate(inv.get("line_items", [])):
        b = locate(doc, li.get("sku")) if li.get("sku") else None
        b = b or locate(doc, li.get("description", "")[:25])
        if b:
            li["box"] = b
    return {k: v for k, v in out.items() if v}




# --------------------------------------------------------------------------- #
# LLM extraction (optional) - two providers
#   openai    : same approach as ai24c Invoice_Processing - pages rendered at 2x,
#               sent as images to gpt-5-mini, typed output via `instructor`.
#               Works with OpenAI directly or OpenRouter (OPENAI_BASE_URL).
#   anthropic : Claude reads the PDF natively.
# --------------------------------------------------------------------------- #
from pydantic import BaseModel  # noqa: E402


class _Bank(BaseModel):
    bank_name: str | None = None
    account_number: str | None = None
    routing_number: str | None = None


class _Line(BaseModel):
    sku: str | None = None
    description: str
    qty: float | None = None
    unit_price: float | None = None
    amount: float


class _Conf(BaseModel):
    vendor_name: float = 0
    invoice_number: float = 0
    invoice_date: float = 0
    po_number: float = 0
    subtotal: float = 0
    tax_amount: float = 0
    total: float = 0


class GenericInvoice(BaseModel):
    vendor_name: str | None = None
    vendor_tax_id: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    po_number: str | None = None
    currency: str | None = None
    subtotal: float | None = None
    tax_amount: float | None = None
    tax_rate: float | None = None
    tax_inclusive: bool = False
    total: float | None = None
    bank: _Bank = _Bank()
    line_items: list[_Line] = []
    cues: list[str] = []
    confidence: _Conf = _Conf()


class PageClass(BaseModel):
    invoice_number: str | None = None
    vendor_name: str | None = None
    is_continuation: bool = False
    is_invoice: bool = True


LLM_PROMPT = """You are the extraction step of an accounts-payable pipeline. Read the attached vendor invoice and
return the fields exactly as printed. Rules:
- Never invent values. If a field is not on the document, return null and set its confidence to 0.
- invoice_number: the vendor's invoice identifier, exactly as printed (do not reformat).
- po_number: the buyer's purchase-order reference if one appears anywhere (labels vary: PO, Purchase Order, Your Ref, Customer PO). null if absent.
- Dates as YYYY-MM-DD.
- Amounts as plain numbers (no symbols). tax_inclusive=true only when the document says the total already includes tax.
- line_items: one entry per billed line, qty * unit_price should equal amount.
- bank: the remit-to details printed on the invoice.
- cues: short verbatim quotes of anything an AP clerk should notice (changed bank details, urgency, reminders, 'statement' instead of invoice).
- confidence: your confidence 0-1 that each field was read correctly."""

PAGE_PROMPT = """This is one page of a PDF that may contain several vendor invoices back to back.
Return the invoice number and vendor name printed on THIS page (null if none), whether this page is a
continuation of the previous invoice (e.g. 'Page 2 of 3', a totals-only page, no new invoice header), and
whether it is an invoice page at all. When in doubt, it is a NEW invoice - splitting is safer than merging."""


def llm_provider() -> str | None:
    """Which LLM is configured. LLM_PROVIDER wins; otherwise whichever key is present (OpenAI first, like ai24c)."""
    p = config.LLM_PROVIDER
    if p == "openai":
        return "openai" if config.OPENAI_API_KEY else None
    if p == "anthropic":
        return "anthropic" if config.ANTHROPIC_API_KEY else None
    if config.OPENAI_API_KEY:
        return "openai"
    if config.ANTHROPIC_API_KEY:
        return "anthropic"
    return None


def llm_available() -> bool:
    return llm_provider() is not None


def llm_model() -> str | None:
    p = llm_provider()
    return config.OPENAI_MODEL if p == "openai" else config.LLM_MODEL if p == "anthropic" else None


def page_images_b64(path: str, pages: list[int] | None = None, zoom: float = 2.0) -> list[str]:
    """Render pages at 2x (as ai24c does) and return base64 PNGs."""
    pdf = pymupdf.open(path)
    out = []
    for i, page in enumerate(pdf):
        if pages and i + 1 not in pages:
            continue
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        out.append(base64.standard_b64encode(pix.tobytes("png")).decode())
    return out


def _openai_client():
    import instructor
    from openai import OpenAI

    kwargs = {"api_key": config.OPENAI_API_KEY}
    if config.OPENAI_BASE_URL:
        kwargs["base_url"] = config.OPENAI_BASE_URL
    return instructor.from_openai(OpenAI(**kwargs))


def _openai_extract(path: str) -> tuple[GenericInvoice, dict]:
    client = _openai_client()
    images = [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b}"}} for b in page_images_b64(path)]
    resp, raw = client.chat.completions.create_with_completion(
        model=config.OPENAI_MODEL,
        response_model=GenericInvoice,
        max_retries=2,
        messages=[{"role": "system", "content": LLM_PROMPT},
                  {"role": "user", "content": [{"type": "text", "text": "Extract this invoice."}, *images]}],
    )
    usage = getattr(raw, "usage", None)
    return resp, {"model": config.OPENAI_MODEL, "provider": "openai",
                  "input_tokens": getattr(usage, "prompt_tokens", None),
                  "output_tokens": getattr(usage, "completion_tokens", None)}


def _anthropic_extract(path: str) -> tuple[GenericInvoice, dict]:
    import anthropic

    data = base64.standard_b64encode(open(path, "rb").read()).decode()
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    resp = client.messages.parse(
        model=config.LLM_MODEL,
        max_tokens=16000,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}},
            {"type": "text", "text": LLM_PROMPT},
        ]}],
        output_format=GenericInvoice,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError(f"LLM returned no structured output (stop_reason={resp.stop_reason})")
    return resp.parsed_output, {"model": config.LLM_MODEL, "provider": "anthropic",
                                "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}


def llm_extract(path: str) -> dict[str, Any]:
    provider = llm_provider()
    if provider is None:
        raise RuntimeError("No LLM key configured")
    parsed, usage = _openai_extract(path) if provider == "openai" else _anthropic_extract(path)
    inv = parsed.model_dump()
    conf = inv.pop("confidence")
    inv["field_confidence"] = {k: round(float(v), 3) for k, v in conf.items()}
    inv["cues"] = [{"type": "llm", "evidence": c} for c in inv["cues"]]
    inv["po_number"] = normalize_po(inv["po_number"]) if inv["po_number"] else None
    inv["invoice_date"] = parse_date(inv["invoice_date"]) or inv["invoice_date"]
    inv["due_date"] = parse_date(inv["due_date"]) or inv["due_date"]
    inv["currency"] = (inv.get("currency") or "").upper()[:3] or None
    for li in inv["line_items"]:
        li["confidence"] = conf.get("subtotal", 0.9)
    inv["llm_usage"] = usage
    return inv


def llm_classify_page(path: str, page_no: int) -> dict:
    """ai24c-style page classification (one vision call per page). OpenAI provider only."""
    client = _openai_client()
    img = page_images_b64(path, [page_no], zoom=1.5)[0]
    resp = client.chat.completions.create(
        model=config.OPENAI_MODEL, response_model=PageClass, max_retries=1,
        messages=[{"role": "user", "content": [{"type": "text", "text": PAGE_PROMPT},
                                               {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}}]}],
    )
    return resp.model_dump()


# --------------------------------------------------------------------------- #
# Multi-invoice PDFs: split pages into invoices
# --------------------------------------------------------------------------- #
CONT_RE = re.compile(r"\bpage\s*([2-9]|\d{2,})\s*(?:of|/)\s*\d+\b|continued\s*from|carried\s*forward|brought\s*forward", re.I)


def classify_pages(path: str, log=lambda m: None) -> list[dict]:
    """Per page: printed invoice number + whether it continues the previous page."""
    pdf = pymupdf.open(path)
    out = []
    use_llm = llm_provider() == "openai" and config.EXTRACTION_MODE != "local"
    for i, page in enumerate(pdf, start=1):
        info = None
        if use_llm:
            try:
                info = llm_classify_page(path, i)
                info["source"] = "llm"
            except Exception as e:
                log(f"Page {i}: LLM classification failed ({type(e).__name__}); using local rules")
                use_llm = False
        if info is None:
            single = pymupdf.open()
            single.insert_pdf(pdf, from_page=i - 1, to_page=i - 1)
            tmp = config.UPLOAD_DIR / f"_page_{uuid_hex()}.pdf"
            single.save(tmp)
            single.close()
            try:
                has_text = len(page.get_text(flags=TEXT_FLAGS).strip()) > 40
                doc = read_text_layer(str(tmp)) if has_text else read_with_ocr(str(tmp))
                parsed = parse_rows(doc)
                text = doc.full_text
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            info = {"invoice_number": parsed.get("invoice_number"), "vendor_name": parsed.get("vendor_name"),
                    "is_continuation": bool(CONT_RE.search(text)) or (not parsed.get("invoice_number") and i > 1),
                    "is_invoice": True, "source": "local"}
        out.append(info)
    return out


def group_pages(pages: list[dict]) -> list[list[int]]:
    """Same grouping rule as ai24c: a different invoice number starts a new invoice; continuations attach."""
    groups: list[list[int]] = []
    current_no = None
    for i, p in enumerate(pages, start=1):
        no = db_norm(p.get("invoice_number"))
        new = (not groups
               or (no and current_no and no != current_no)
               or (no and not current_no and not p.get("is_continuation")))
        if new:
            groups.append([i])
        else:
            groups[-1].append(i)
        if no:
            current_no = no
    return groups


def split_pdf(path: str, groups: list[list[int]], stem: str) -> list[tuple[str, str]]:
    """Write one PDF per invoice; returns (path, display name)."""
    pdf = pymupdf.open(path)
    out = []
    for g in groups:
        part = pymupdf.open()
        part.insert_pdf(pdf, from_page=g[0] - 1, to_page=g[-1] - 1)
        label = str(g[0]) if len(g) == 1 else f"{g[0]}-{g[-1]}"
        dest = config.UPLOAD_DIR / f"{uuid_hex()[:8]}_{stem}_p{label}.pdf"
        part.save(dest)
        part.close()
        out.append((str(dest), f"{stem}.pdf ({'page' if len(g) == 1 else 'pages'} {label})"))
    return out


def _short_err(e: Exception) -> str:
    s = str(e)
    m = re.search(r"Error code: \d+[^{]*(?:\{.*?'message': '([^']{0,90}))?", s)
    if m:
        return f"{type(e).__name__}: {m.group(0).split(' - ')[0]}" + (f" — {m.group(1)}" if m.group(1) else "")
    first = next((ln.strip() for ln in s.splitlines() if ln.strip() and not ln.strip().startswith("<")), "")
    return f"{type(e).__name__}: {first[:120]}"


def uuid_hex() -> str:
    import uuid
    return uuid.uuid4().hex


def db_norm(s: str | None) -> str:
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def choose_method(has_text_layer: bool) -> str:
    mode = config.EXTRACTION_MODE
    if mode == "llm" or (mode == "auto" and llm_available()):
        return "llm"
    return "text" if has_text_layer else "ocr"


def extract(path: str, has_text_layer: bool, log) -> dict[str, Any]:
    method = choose_method(has_text_layer)
    log(f"Extraction method: {method}")
    if method == "llm":
        if llm_provider() == "openai":
            log(f"Rendering pages at 2x and sending to {config.OPENAI_MODEL} (OpenAI vision + instructor, as in ai24c)")
        else:
            log(f"Sending PDF to {llm_model()} for structured extraction")
        try:
            result = llm_extract(path)
        except Exception as e:  # network / auth / quota / refusal -> degrade to local readers
            log(f"LLM extraction failed ({_short_err(e)}); falling back to local reader")
            method = "text" if has_text_layer else "ocr"
        else:
            result["method"] = "llm"
            # The local reader still runs: it gives field coordinates for the PDF highlight
            # and an independent second reading to cross-check the model against.
            log("Locating extracted values on the page" + (" and cross-checking against the text layer" if has_text_layer else " (OCR)"))
            doc = read_text_layer(path) if has_text_layer else read_with_ocr(path, log)
            result["field_boxes"] = boxes_for(doc, result)
            if has_text_layer:
                local = parse_rows(doc)
                result["cross_check"] = cross_check(result, local)
                result["cues"].extend(c for c in local["cues"] if c["type"] != "llm")
                log(f"Cross-check: {result['cross_check']['agreement']}")
            return result
    if method == "text":
        log("Rebuilding visual rows from the PDF text layer (PyMuPDF word coordinates)")
        doc = read_text_layer(path)
    else:
        engine = ocr_engine_name()
        log("No text layer: rasterising pages at 200 dpi and running OCR "
            + ("(Google Cloud Vision, as in ai24c)" if engine == "google-vision" else "(local RapidOCR)"))
        doc = read_with_ocr(path, log)
        log(f"OCR mean confidence {doc.ocr_confidence:.2f} across {len(doc.rows)} rows")
    result = parse_rows(doc)
    result["method"] = method
    if method == "ocr":
        result["ocr_confidence"] = doc.ocr_confidence
        result["ocr_engine"] = ocr_engine_name()
    log(f"Located {len(result.get('field_boxes', {}))} fields on the page for highlighting")
    return result


def cross_check(a: dict, b: dict) -> dict[str, Any]:
    fields = ["invoice_number", "invoice_date", "po_number", "subtotal", "tax_amount", "total"]
    diffs = []
    for f in fields:
        va, vb = a.get(f), b.get(f)
        if va is None or vb is None:
            continue
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            same = abs(float(va) - float(vb)) <= 0.01
        else:
            same = str(va).replace(" ", "").upper() == str(vb).replace(" ", "").upper()
        if not same:
            diffs.append({"field": f, "llm": va, "text_layer": vb})
    return {"agreement": "full" if not diffs else f"{len(diffs)} field(s) disagree", "differences": diffs}


def today() -> date:
    if config.AP_TODAY:
        return datetime.strptime(config.AP_TODAY, "%Y-%m-%d").date()
    return date.today()
