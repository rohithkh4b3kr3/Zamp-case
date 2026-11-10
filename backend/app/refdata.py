"""Procurement reference data: replace the AP team's PO spreadsheet.

* Import the spreadsheets AP already keeps (CSV or Excel) for purchase orders, goods receipts and the
  vendor master: columns are mapped automatically (editable), every row is validated, and the user sees
  exactly what will change (new / updated / unchanged / errors) before anything is written.
* Link a live sheet (Google Sheets, OneDrive, any CSV URL): re-read on a schedule, valid rows merged.
* Edit POs and add goods receipts in the app. Every change goes to the audit log ("History").
"""
from __future__ import annotations

import copy
import csv
import io
import ipaddress
import json
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
import uuid
from typing import Any

from rapidfuzz import fuzz

from . import config, db

# --------------------------------------------------------------------------- field specs
FIELDS: dict[str, list[dict]] = {
    "po": [
        {"id": "po_number", "label": "PO number", "required": True, "syn": ["po", "ponumber", "pono", "purchaseorder", "purchaseordernumber", "ponum", "order", "ordernumber"]},
        {"id": "vendor", "label": "Vendor (ID or name)", "required": True, "syn": ["vendor", "vendorname", "vendorid", "supplier", "suppliername", "supplierid"]},
        {"id": "date", "label": "PO date", "syn": ["date", "podate", "orderdate", "created", "createdon"]},
        {"id": "status", "label": "Status", "syn": ["status", "postatus", "state"]},
        {"id": "currency", "label": "Currency", "syn": ["currency", "curr", "ccy"]},
        {"id": "tax_rate", "label": "Tax rate", "syn": ["taxrate", "tax", "taxpercent", "vat", "vatrate", "gst"]},
        {"id": "requester", "label": "Requester", "syn": ["requester", "requestedby", "buyer", "owner", "department"]},
        {"id": "line", "label": "Line no.", "syn": ["line", "lineno", "linenumber", "itemno", "linenum", "pos"]},
        {"id": "sku", "label": "SKU / part no.", "syn": ["sku", "partno", "partnumber", "itemcode", "material", "productcode", "part"]},
        {"id": "description", "label": "Description", "required": True, "syn": ["description", "desc", "item", "itemdescription", "product", "service"]},
        {"id": "qty", "label": "Quantity", "required": True, "syn": ["qty", "quantity", "orderedqty", "qtyordered", "orderqty"]},
        {"id": "unit_price", "label": "Unit price", "required": True, "syn": ["unitprice", "price", "rate", "unitcost", "cost", "priceperunit"]},
    ],
    "grn": [
        {"id": "grn", "label": "Receipt no.", "syn": ["grn", "receipt", "receiptno", "receiptnumber", "grnnumber", "goodsreceipt", "grnno"]},
        {"id": "po_number", "label": "PO number", "required": True, "syn": ["po", "ponumber", "pono", "purchaseorder", "ponum"]},
        {"id": "date", "label": "Receipt date", "syn": ["date", "receiptdate", "receivedon", "receiveddate", "grndate"]},
        {"id": "line", "label": "PO line no.", "required": True, "syn": ["line", "lineno", "linenumber", "poline", "itemno"]},
        {"id": "qty_received", "label": "Qty received", "required": True, "syn": ["qtyreceived", "receivedqty", "received", "quantityreceived", "qty", "quantity"]},
    ],
    "vendor": [
        {"id": "vendor_id", "label": "Vendor ID", "syn": ["vendorid", "supplierid", "id", "vendorcode", "suppliercode"]},
        {"id": "name", "label": "Legal name", "required": True, "syn": ["name", "vendorname", "suppliername", "legalname", "vendor", "supplier", "company"]},
        {"id": "aliases", "label": "Other names (; separated)", "syn": ["aliases", "aka", "othernames", "tradingnames", "dba"]},
        {"id": "tax_id", "label": "Tax ID / VAT", "syn": ["taxid", "ein", "vat", "vatid", "tin", "vatnumber", "taxnumber"]},
        {"id": "status", "label": "Status", "syn": ["status", "vendorstatus", "state"]},
        {"id": "payment_terms", "label": "Payment terms", "syn": ["terms", "paymentterms", "payterms"]},
        {"id": "currency", "label": "Currency", "syn": ["currency", "curr", "ccy"]},
        {"id": "email", "label": "Billing email", "syn": ["email", "billingemail", "apemail", "mail", "contactemail"]},
        {"id": "phone", "label": "Phone", "syn": ["phone", "telephone", "tel", "contactphone"]},
        {"id": "bank_name", "label": "Bank name", "syn": ["bank", "bankname"]},
        {"id": "account_number", "label": "Account no. / IBAN", "syn": ["account", "accountnumber", "accountno", "iban", "bankaccount"]},
        {"id": "routing_number", "label": "Routing / ABA", "syn": ["routing", "aba", "routingnumber", "sortcode", "bic", "swift"]},
    ],
}
KIND_LABEL = {"po": "purchase orders", "grn": "goods receipts", "vendor": "vendors"}
TEMPLATE_ROWS = {
    "po": [["PO-2026-0500", "V-1001", "2026-11-01", "open", "USD", "0.08", "Priya Raman", "1", "NW-CH-220", "Ergonomic mesh office chair", "10", "185.00"],
           ["PO-2026-0500", "V-1001", "2026-11-01", "open", "USD", "0.08", "Priya Raman", "2", "NW-PP-A4", "A4 copy paper (ream)", "50", "6.50"]],
    "grn": [["GRN-90001", "PO-2026-0500", "2026-11-08", "1", "10"]],
    "vendor": [["V-1010", "Example Supplies Ltd", "Example Supplies; ExSup", "12-3456789", "approved", "Net 30", "USD",
                "billing@example.com", "+1 555 0100", "First Bank", "000111222333", "021000021"]],
}


def norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


# --------------------------------------------------------------------------- reading files
class ImportErrorUser(ValueError):
    """Message is shown to the user as 'Import failed: <message>'."""


def read_table(data: bytes, filename: str = "") -> tuple[list[str], list[dict[str, str]]]:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")) or data[:2] == b"PK":
        return _read_xlsx(data)
    if name.endswith(".xls"):
        raise ImportErrorUser("old .xls files are not supported. Save the sheet as .xlsx or .csv and upload again.")
    return _read_csv(data)


def _read_csv(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text.lstrip().startswith("<"):
        raise ImportErrorUser("the link returned a web page, not CSV. Use the sheet's CSV export / 'publish to web' link.")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise ImportErrorUser("the file has no data rows. Put column names in the first row and one record per row.")
    headers = [h.strip() for h in rows[0]]
    return headers, [{headers[i]: (r[i].strip() if i < len(r) else "") for i in range(len(headers))} for r in rows[1:]]


def _read_xlsx(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise ImportErrorUser(f"could not open the Excel file ({type(e).__name__}). Re-save it as .xlsx or .csv.")
    ws = wb.worksheets[0]
    rows = []
    for r in ws.iter_rows(values_only=True):
        cells = ["" if v is None else (v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v)).strip() for v in r]
        if any(cells):
            rows.append(cells)
    if len(rows) < 2:
        raise ImportErrorUser("the first sheet has no data rows. Put column names in the first row.")
    headers = rows[0]
    return headers, [{headers[i]: (r[i] if i < len(r) else "") for i in range(len(headers)) if headers[i]} for r in rows[1:]]


def auto_map(kind: str, headers: list[str]) -> dict[str, str | None]:
    """field id -> file column (or None)."""
    taken: set[str] = set()
    out: dict[str, str | None] = {f["id"]: None for f in FIELDS[kind]}
    # pass 1: exact name / synonym matches for every field, so a fuzzy guess never steals an exact column
    for f in FIELDS[kind]:
        for h in headers:
            if h not in taken and (norm(h) == norm(f["id"]) or norm(h) in f["syn"]):
                out[f["id"]] = h
                taken.add(h)
                break
    # pass 2: fuzzy fallback for fields still unmapped
    for f in FIELDS[kind]:
        if out[f["id"]]:
            continue
        scored = [(fuzz.ratio(norm(h), s), h) for h in headers if h not in taken for s in f["syn"]]
        scored.sort(reverse=True)
        if scored and scored[0][0] >= 88:
            out[f["id"]] = scored[0][1]
            taken.add(scored[0][1])
    return out


# --------------------------------------------------------------------------- building records
def _num(v: str) -> float | None:
    s = (v or "").replace(",", "").replace("$", "").replace("€", "").replace("£", "").strip()
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _resolve_vendor(value: str, vendors: list[dict]) -> str | None:
    v = (value or "").strip()
    if not v:
        return None
    for x in vendors:
        if x["vendor_id"].lower() == v.lower():
            return x["vendor_id"]
    best, score = None, 0
    for x in vendors:
        for cand in [x["name"], *x.get("aliases", [])]:
            s = fuzz.token_set_ratio(v.lower(), cand.lower())
            if s > score:
                best, score = x["vendor_id"], s
    return best if score >= 88 else None


def build(kind: str, rows: list[dict[str, str]], mapping: dict[str, str | None], ref: dict) -> tuple[list[dict], list[dict]]:
    """Rows -> objects (POs grouped from lines). Returns (objects, errors[{row, field, message}])."""
    errors: list[dict] = []
    missing = [f["label"] for f in FIELDS[kind] if f.get("required") and not mapping.get(f["id"])]
    if missing:
        raise ImportErrorUser(f"no column mapped to {', '.join(missing)}. Map those columns in step 2.")

    def g(row, fid):
        col = mapping.get(fid)
        return (row.get(col) or "").strip() if col else ""

    if kind == "vendor":
        out, seen = [], {}
        existing_ids = [v["vendor_id"] for v in ref["vendors"]]
        for i, r in enumerate(rows, start=2):
            name = g(r, "name")
            if not name:
                errors.append({"row": i, "field": "name", "message": "Legal name is empty"})
                continue
            vid = g(r, "vendor_id") or next((v["vendor_id"] for v in ref["vendors"] if v["name"].lower() == name.lower()), None)
            if not vid:
                n = 1001 + len(existing_ids) + len(out)
                while f"V-{n}" in existing_ids:
                    n += 1
                vid = f"V-{n}"
            status = (g(r, "status") or "approved").lower()
            if status not in ("approved", "suspended", "blocked", "pending"):
                errors.append({"row": i, "field": "status", "message": f"Status '{status}' must be approved, pending, suspended or blocked"})
                continue
            email = g(r, "email")
            if email and "@" not in email:
                errors.append({"row": i, "field": "email", "message": f"'{email}' is not an email address"})
                continue
            obj = {"vendor_id": vid, "name": name,
                   "aliases": [a.strip() for a in g(r, "aliases").replace(",", ";").split(";") if a.strip()],
                   "tax_id": g(r, "tax_id"), "status": status, "payment_terms": g(r, "payment_terms") or "Net 30",
                   "currency": (g(r, "currency") or "USD").upper()[:3], "email": email, "phone": g(r, "phone"),
                   "bank": {"bank_name": g(r, "bank_name"), "account_number": re.sub(r"\s", "", g(r, "account_number")),
                            "routing_number": g(r, "routing_number") or None}}
            if vid in seen:
                errors.append({"row": i, "field": "vendor_id", "message": f"{vid} appears twice in the file; the later row wins"})
            seen[vid] = obj
        return list(seen.values()), errors

    if kind == "grn":
        groups: dict[str, dict] = {}
        po_numbers = {p["po_number"] for p in ref["purchase_orders"]}
        for i, r in enumerate(rows, start=2):
            po = g(r, "po_number").upper()
            if po not in po_numbers:
                errors.append({"row": i, "field": "po_number", "message": f"PO '{po or '(empty)'}' does not exist. Import the PO first."})
                continue
            line, qty = _num(g(r, "line")), _num(g(r, "qty_received"))
            if line is None or qty is None or qty < 0:
                errors.append({"row": i, "field": "qty_received", "message": "Line and received quantity must be numbers ≥ 0"})
                continue
            po_obj = next(p for p in ref["purchase_orders"] if p["po_number"] == po)
            if int(line) not in {l["line"] for l in po_obj["lines"]}:
                errors.append({"row": i, "field": "line", "message": f"{po} has no line {int(line)}"})
                continue
            gid = g(r, "grn") or f"GRN-{po[-4:]}-{g(r, 'date') or 'import'}"
            grp = groups.setdefault(gid, {"grn": gid, "po_number": po, "date": g(r, "date") or time.strftime("%Y-%m-%d"), "lines": []})
            grp["lines"].append({"line": int(line), "qty_received": qty if not float(qty).is_integer() else int(qty)})
        return list(groups.values()), errors

    # purchase orders: one row per PO line
    groups: dict[str, dict] = {}
    for i, r in enumerate(rows, start=2):
        po = g(r, "po_number").upper()
        if not po:
            errors.append({"row": i, "field": "po_number", "message": "PO number is empty"})
            continue
        vid = _resolve_vendor(g(r, "vendor"), ref["vendors"])
        if not vid:
            errors.append({"row": i, "field": "vendor", "message": f"Vendor '{g(r, 'vendor')}' is not in the vendor master. Import vendors first or use the vendor ID."})
            continue
        qty, price = _num(g(r, "qty")), _num(g(r, "unit_price"))
        if qty is None or qty <= 0:
            errors.append({"row": i, "field": "qty", "message": f"Quantity '{g(r, 'qty')}' must be a number > 0"})
            continue
        if price is None or price < 0:
            errors.append({"row": i, "field": "unit_price", "message": f"Unit price '{g(r, 'unit_price')}' must be a number ≥ 0"})
            continue
        status = (g(r, "status") or "open").lower()
        if status not in ("open", "closed", "cancelled"):
            errors.append({"row": i, "field": "status", "message": f"Status '{status}' must be open, closed or cancelled"})
            continue
        tax = _num(g(r, "tax_rate"))
        if tax is not None and tax > 1:
            tax = tax / 100  # "8" means 8%
        po_obj = groups.setdefault(po, {"po_number": po, "vendor_id": vid, "date": g(r, "date") or time.strftime("%Y-%m-%d"),
                                        "status": status, "currency": (g(r, "currency") or "USD").upper()[:3],
                                        "requester": g(r, "requester"), "tax_rate": tax if tax is not None else 0.0, "lines": []})
        if po_obj["vendor_id"] != vid:
            errors.append({"row": i, "field": "vendor", "message": f"{po} has lines for two different vendors"})
            continue
        ln = _num(g(r, "line"))
        line_no = int(ln) if ln else len(po_obj["lines"]) + 1
        if any(l["line"] == line_no for l in po_obj["lines"]):
            errors.append({"row": i, "field": "line", "message": f"{po} line {line_no} appears twice"})
            continue
        po_obj["lines"].append({"line": line_no, "sku": g(r, "sku"), "description": g(r, "description"),
                                "qty": qty if not float(qty).is_integer() else int(qty), "unit_price": round(price, 4)})
    return list(groups.values()), errors


LIST_KEY = {"po": ("purchase_orders", "po_number"), "grn": ("goods_receipts", "grn"), "vendor": ("vendors", "vendor_id")}


def diff(kind: str, objects: list[dict], ref: dict) -> dict:
    lk, key = LIST_KEY[kind]
    current = {o[key]: o for o in ref[lk]}
    new = [o[key] for o in objects if o[key] not in current]
    upd = [o[key] for o in objects if o[key] in current and _strip(current[o[key]]) != _strip(o)]
    same = [o[key] for o in objects if o[key] in current and _strip(current[o[key]]) == _strip(o)]
    return {"new": new, "updated": upd, "unchanged": same, "removed_if_replace": [k for k in current if k not in {o[key] for o in objects}]}


def _strip(o: dict) -> str:
    return json.dumps({k: v for k, v in o.items() if k not in ("status_reason",)}, sort_keys=True, default=str)


def apply(kind: str, objects: list[dict], mode: str, actor: str, source: str) -> dict:
    ref = copy.deepcopy(config.load_reference())
    lk, key = LIST_KEY[kind]
    d = diff(kind, objects, ref)
    if mode == "replace":
        ref[lk] = objects
    else:
        by = {o[key]: o for o in ref[lk]}
        for o in objects:
            by[o[key]] = {**by.get(o[key], {}), **o}
        ref[lk] = list(by.values())
    config.save_reference(ref)
    summary = {"new": len(d["new"]), "updated": len(d["updated"]), "unchanged": len(d["unchanged"]),
               "removed": len(d["removed_if_replace"]) if mode == "replace" else 0}
    db.audit(None, actor, f"ref_import_{kind}",
             f"{source}: {summary['new']} new, {summary['updated']} updated, {summary['unchanged']} unchanged"
             + (f", {summary['removed']} removed (replace)" if summary["removed"] else "")
             + (f" · new: {', '.join(d['new'][:8])}" if d["new"] else "") + (f" · updated: {', '.join(d['updated'][:8])}" if d["updated"] else ""))
    return summary


# --------------------------------------------------------------------------- upload sessions (preview -> commit)
_uploads: dict[str, dict] = {}


def stage_upload(kind: str, data: bytes, filename: str) -> dict:
    if kind not in FIELDS:
        raise ImportErrorUser("choose what you are importing: purchase orders, goods receipts or vendors.")
    headers, rows = read_table(data, filename)
    if len(rows) > 20000:
        raise ImportErrorUser("the file has more than 20,000 rows. Split it into smaller files.")
    token = uuid.uuid4().hex
    for t in [t for t, u in _uploads.items() if time.time() - u["ts"] > 3600]:
        _uploads.pop(t, None)
    _uploads[token] = {"kind": kind, "headers": headers, "rows": rows, "filename": filename, "ts": time.time()}
    return {"token": token, "kind": kind, "filename": filename, "headers": headers, "row_count": len(rows),
            "sample": rows[:5], "mapping": auto_map(kind, headers), "fields": FIELDS_PUBLIC[kind]}


def preview(token: str, mapping: dict | None) -> dict:
    u = _uploads.get(token)
    if not u:
        raise ImportErrorUser("the upload expired. Upload the file again.")
    mapping = mapping or auto_map(u["kind"], u["headers"])
    ref = config.load_reference()
    objects, errors = build(u["kind"], u["rows"], mapping, ref)
    d = diff(u["kind"], objects, ref)
    u["mapping"], u["objects"] = mapping, objects
    lk, key = LIST_KEY[u["kind"]]
    return {"kind": u["kind"], "mapping": mapping, "objects": len(objects), "rows": len(u["rows"]),
            "new": d["new"], "updated": d["updated"], "unchanged": d["unchanged"], "removed_if_replace": d["removed_if_replace"],
            "errors": errors[:200], "error_count": len(errors), "items": objects[:50]}


def commit(token: str, mapping: dict | None, mode: str, actor: str = "user") -> dict:
    if mode not in ("merge", "replace"):
        raise ImportErrorUser("mode must be merge or replace.")
    p = preview(token, mapping)
    u = _uploads.pop(token)
    if not u["objects"]:
        raise ImportErrorUser("no valid rows to import. Fix the errors listed and upload again.")
    summary = apply(u["kind"], u["objects"], mode, actor, f"file {u['filename']}")
    return {**summary, "skipped_rows": p["error_count"], "kind": u["kind"], "label": KIND_LABEL[u["kind"]]}


FIELDS_PUBLIC = {k: [{"id": f["id"], "label": f["label"], "required": bool(f.get("required"))} for f in v] for k, v in FIELDS.items()}


def template(kind: str, fmt: str = "csv") -> tuple[bytes, str]:
    headers = [f["id"] for f in FIELDS[kind]]
    rows = TEMPLATE_ROWS[kind]
    if fmt == "xlsx":
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = KIND_LABEL[kind]
        ws.append(headers)
        for r in rows:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8"), "text/csv"


# --------------------------------------------------------------------------- live sources
SOURCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS ref_sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    url TEXT NOT NULL,
    interval_min INTEGER NOT NULL,
    mapping_json TEXT,
    last_sync TEXT,
    last_status TEXT,
    last_message TEXT,
    created_at TEXT NOT NULL
);
"""


def _init_sources():
    with db._lock:
        db.conn().executescript(SOURCES_SCHEMA)
        db.conn().commit()


def normalise_url(url: str) -> str:
    """Turn share links into direct CSV downloads (Google Sheets, OneDrive / SharePoint)."""
    u = url.strip()
    m = re.match(r"https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)", u)
    if m and "format=csv" not in u and "output=csv" not in u:
        gid = re.search(r"[#&?]gid=(\d+)", u)
        return f"https://docs.google.com/spreadsheets/d/{m[1]}/export?format=csv" + (f"&gid={gid[1]}" if gid else "")
    if ("1drv.ms" in u or "sharepoint.com" in u or "onedrive.live.com" in u) and "download=1" not in u:
        return u + ("&" if "?" in u else "?") + "download=1"
    return u


def _check_url(url: str) -> None:
    p = urllib.parse.urlparse(url)
    if p.scheme != "https" or not p.hostname:
        raise ImportErrorUser("the link must start with https://.")
    try:
        for info in socket.getaddrinfo(p.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ImportErrorUser("links to internal network addresses are not allowed.")
    except socket.gaierror:
        raise ImportErrorUser(f"cannot resolve {p.hostname}. Check the link.")


def fetch(url: str) -> tuple[bytes, str]:
    url = normalise_url(url)
    _check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "AP-Autopilot/1.0 (sheet sync)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read(25 * 1024 * 1024 + 1)
            ctype = r.headers.get("content-type", "")
    except Exception as e:
        raise ImportErrorUser(f"could not download the sheet ({type(e).__name__}: {str(e)[:120]}). Check the link is shared publicly or published as CSV.")
    if len(data) > 25 * 1024 * 1024:
        raise ImportErrorUser("the sheet is larger than 25 MB.")
    name = "sheet.xlsx" if "spreadsheetml" in ctype else "sheet.csv"
    return data, name


def list_sources() -> list[dict]:
    _init_sources()
    with db._lock:
        rows = db.conn().execute("SELECT * FROM ref_sources ORDER BY created_at").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["mapping"] = json.loads(d.pop("mapping_json") or "null")
        out.append(d)
    return out


def add_source(name: str, kind: str, url: str, interval_min: int) -> dict:
    _init_sources()
    if kind not in FIELDS:
        raise ImportErrorUser("choose purchase orders, goods receipts or vendors.")
    if not name.strip():
        raise ImportErrorUser("give the source a name.")
    interval_min = max(5, min(int(interval_min or 60), 1440))
    _check_url(normalise_url(url))
    sid = uuid.uuid4().hex[:10]
    with db._lock:
        db.conn().execute("INSERT INTO ref_sources(id, name, kind, url, interval_min, created_at) VALUES (?,?,?,?,?,?)",
                          (sid, name.strip()[:80], kind, url.strip(), interval_min, db.now_iso()))
        db.conn().commit()
    db.audit(None, "user", "ref_source_added", f"{name} ({KIND_LABEL[kind]}) every {interval_min} min: {url}")
    return sync_source(sid)


def remove_source(sid: str) -> bool:
    _init_sources()
    with db._lock:
        cur = db.conn().execute("DELETE FROM ref_sources WHERE id=?", (sid,))
        db.conn().commit()
    if cur.rowcount:
        db.audit(None, "user", "ref_source_removed", sid)
    return cur.rowcount > 0


def sync_source(sid: str) -> dict:
    s = next((x for x in list_sources() if x["id"] == sid), None)
    if not s:
        raise KeyError(sid)
    status, msg = "ok", ""
    try:
        data, fname = fetch(s["url"])
        headers, rows = read_table(data, fname)
        mapping = s["mapping"] or auto_map(s["kind"], headers)
        objects, errors = build(s["kind"], rows, mapping, config.load_reference())
        if not objects:
            raise ImportErrorUser(f"no valid rows ({len(errors)} errors). First: {errors[0]['message'] if errors else 'empty sheet'}")
        summary = apply(s["kind"], objects, "merge", "sync", f"sheet '{s['name']}'")
        status = "partial" if errors else "ok"
        msg = f"{summary['new']} new, {summary['updated']} updated, {summary['unchanged']} unchanged" + (f"; {len(errors)} row(s) skipped" if errors else "")
    except ImportErrorUser as e:
        status, msg = "error", str(e)
    except Exception as e:
        status, msg = "error", f"{type(e).__name__}: {e}"
    with db._lock:
        db.conn().execute("UPDATE ref_sources SET last_sync=?, last_status=?, last_message=? WHERE id=?",
                          (db.now_iso(), status, msg[:400], sid))
        db.conn().commit()
    return next(x for x in list_sources() if x["id"] == sid)


_started = False


def start_sync_loop() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        from datetime import datetime, timezone
        while True:
            try:
                for s in list_sources():
                    last = datetime.fromisoformat(s["last_sync"]) if s["last_sync"] else None
                    if last is None or (datetime.now(timezone.utc) - last).total_seconds() >= s["interval_min"] * 60:
                        sync_source(s["id"])
            except Exception:
                pass
            time.sleep(60)

    threading.Thread(target=loop, daemon=True, name="sheet-sync").start()


# --------------------------------------------------------------------------- in-app edits
def po_detail(po_number: str) -> dict | None:
    ref = config.load_reference()
    po = next((p for p in ref["purchase_orders"] if p["po_number"] == po_number), None)
    if not po:
        return None
    vendor = next((v for v in ref["vendors"] if v["vendor_id"] == po["vendor_id"]), {})
    receipts = [g for g in ref["goods_receipts"] if g["po_number"] == po_number]
    ledger = db.ledger_entries(po_number=po_number)
    rec, billed = {}, {}
    for g in receipts:
        for l in g["lines"]:
            rec[l["line"]] = rec.get(l["line"], 0) + l["qty_received"]
    for e in ledger:
        for l in e["lines"]:
            billed[l["po_line"]] = billed.get(l["po_line"], 0) + (l.get("qty") or 0)
    total = round(sum(l["qty"] * l["unit_price"] for l in po["lines"]), 2)
    billed_amt = round(sum(e["subtotal"] or 0 for e in ledger), 2)
    return {**po, "vendor_name": vendor.get("name"), "total": total, "billed": billed_amt, "remaining": round(total - billed_amt, 2),
            "lines": [{**l, "received": rec.get(l["line"], 0), "billed_qty": billed.get(l["line"], 0)} for l in po["lines"]],
            "receipts": receipts,
            "invoices": [{"invoice_number": e["invoice_number"], "invoice_date": e["invoice_date"], "subtotal": e["subtotal"],
                          "status": e["status"], "run_id": e.get("run_id")} for e in ledger]}


def open_pos(vendor_id: str | None) -> list[dict]:
    ref = config.load_reference()
    out = []
    for p in ref["purchase_orders"]:
        if vendor_id and p["vendor_id"] != vendor_id:
            continue
        d = po_detail(p["po_number"])
        out.append({k: d[k] for k in ("po_number", "vendor_id", "vendor_name", "date", "status", "currency", "total", "billed", "remaining")}
                   | {"lines": [{"line": l["line"], "sku": l.get("sku"), "description": l["description"], "qty": l["qty"],
                                 "unit_price": l["unit_price"]} for l in d["lines"]]})
    return sorted(out, key=lambda x: (x["status"] != "open", x["po_number"]))


def update_po(po_number: str, patch: dict, actor: str = "user") -> dict:
    ref = copy.deepcopy(config.load_reference())
    po = next((p for p in ref["purchase_orders"] if p["po_number"] == po_number), None)
    if not po:
        raise KeyError(po_number)
    changes = []
    for k in ("status", "requester", "date", "currency"):
        if k in patch and patch[k] is not None and str(patch[k]) != str(po.get(k)):
            if k == "status" and patch[k] not in ("open", "closed", "cancelled"):
                raise ImportErrorUser("status must be open, closed or cancelled.")
            changes.append(f"{k}: {po.get(k)} → {patch[k]}")
            po[k] = patch[k]
    if "lines" in patch:
        old = {l["line"]: l for l in po["lines"]}
        new_lines = []
        for l in patch["lines"]:
            try:
                ln = int(l["line"])
                qty = float(l["qty"])
                price = float(l["unit_price"])
            except (KeyError, TypeError, ValueError):
                raise ImportErrorUser("each line needs a line number, a quantity and a unit price.")
            if qty <= 0 or price < 0:
                raise ImportErrorUser(f"line {ln}: quantity must be > 0 and price ≥ 0.")
            desc = str(l.get("description") or "").strip()
            if not desc:
                raise ImportErrorUser(f"line {ln}: description is empty.")
            nl = {"line": ln, "sku": str(l.get("sku") or "").strip(), "description": desc,
                  "qty": int(qty) if qty.is_integer() else qty, "unit_price": round(price, 4)}
            o = old.get(ln)
            if not o:
                changes.append(f"line {ln} added: {nl['qty']} × {nl['unit_price']} {desc}")
            else:
                for k in ("sku", "description", "qty", "unit_price"):
                    if str(o.get(k)) != str(nl[k]):
                        changes.append(f"line {ln} {k}: {o.get(k)} → {nl[k]}")
            new_lines.append(nl)
        for ln in old:
            if ln not in {l["line"] for l in new_lines}:
                changes.append(f"line {ln} removed")
        if len({l["line"] for l in new_lines}) != len(new_lines):
            raise ImportErrorUser("two lines share the same line number.")
        po["lines"] = sorted(new_lines, key=lambda l: l["line"])
    if not changes:
        return po_detail(po_number)
    config.save_reference(ref)
    db.audit(None, actor, "ref_po_updated", f"{po_number}: " + "; ".join(changes))
    return po_detail(po_number)


def add_receipt(po_number: str, lines: list[dict], date: str | None, grn: str | None, actor: str = "user") -> dict:
    ref = copy.deepcopy(config.load_reference())
    po = next((p for p in ref["purchase_orders"] if p["po_number"] == po_number), None)
    if not po:
        raise KeyError(po_number)
    valid = {l["line"] for l in po["lines"]}
    clean = []
    for l in lines:
        try:
            ln, q = int(l["line"]), float(l["qty_received"])
        except (KeyError, TypeError, ValueError):
            raise ImportErrorUser("each received line needs a line number and a quantity.")
        if ln not in valid:
            raise ImportErrorUser(f"{po_number} has no line {ln}.")
        if q < 0:
            raise ImportErrorUser("received quantity cannot be negative.")
        if q > 0:
            clean.append({"line": ln, "qty_received": int(q) if q.is_integer() else q})
    if not clean:
        raise ImportErrorUser("enter a received quantity for at least one line.")
    gid = (grn or "").strip() or f"GRN-{int(time.time()) % 100000:05d}"
    if any(g["grn"] == gid for g in ref["goods_receipts"]):
        raise ImportErrorUser(f"receipt {gid} already exists. Use a different receipt number.")
    rec = {"grn": gid, "po_number": po_number, "date": date or time.strftime("%Y-%m-%d"), "lines": clean}
    ref["goods_receipts"].append(rec)
    config.save_reference(ref)
    db.audit(None, actor, "ref_grn_added", f"{gid} on {po_number}: " + ", ".join(f"line {l['line']} × {l['qty_received']}" for l in clean))
    return po_detail(po_number)


def history(limit: int = 200) -> list[dict]:
    with db._lock:
        rows = db.conn().execute(
            "SELECT * FROM audit WHERE action LIKE 'ref_%' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
