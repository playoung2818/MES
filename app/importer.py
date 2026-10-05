import math
import pandas as pd
import gspread

COLUMN_ALIASES = {
    "sales_order": ["sales order", "so", "so#", "salesorder", "sales order no", "sales order number", "qb num", "qb number"],
    "work_order": ["work order", "wo", "wo#", "workorder", "work order no", "work order number"],
    "customer": ["customer", "customer name", "cust"],
    "customer_po": ["customer po", "cust po", "po", "po#", "customer p/o", "customer purchase order"],
    "item": ["item", "item number", "part number", "pn", "model", "model name"],
    "configuration": ["configuration", "config", "description", "item description"],
    "quantity": ["quantity", "qty", "order qty", "wo qty"],
    "inventory_site": ["inventory site", "site", "warehouse", "location"],
    "ship_date": ["lead time", "ship date", "shipping date", "l/t"],
    "remark": ["remark", "remarks", "note", "notes", "comment", "comments"],
}

def _norm(value):
    return str(value).strip().lower().replace("_", " ")

def _clean(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    value = str(value).strip()
    return value if value and value.lower() != "nan" else None

def _find_columns(columns):
    normalized = {_norm(c): c for c in columns}
    found = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                found[field] = normalized[alias]
                break
    return found

def _read_frames(frames):
    skipped = 0
    messages = []
    grouped = {}
    for sheet_name, df in frames.items():
        cols = _find_columns(df.columns)
        if not {'sales_order', 'item', 'quantity'}.issubset(cols):
            messages.append(f"Skipped sheet '{sheet_name}': missing SO/item/quantity columns")
            continue
        for _, row in df.dropna(how="all").iterrows():
            so = _clean(row.get(cols["sales_order"]))
            product = _clean(row.get(cols["item"]))
            if not so or not product:
                skipped += 1
                continue

            def value(field):
                return _clean(row.get(cols[field])) if field in cols else None

            try:
                number = float(value('quantity') or '')
                if not math.isfinite(number) or not number.is_integer() or number < 0:
                    raise ValueError()
                quantity = int(number)
            except ValueError:
                skipped += 1
                messages.append(f'{so}: invalid quantity for {product}')
                continue
            grouped.setdefault(so, {"sales_order": so, "customer": value("customer"),
                                    "customer_po": value("customer_po"), "items": []})
            order = grouped[so]
            if value("customer") is not None:
                order["customer"] = value("customer")
            if value("customer_po") is not None:
                order["customer_po"] = value("customer_po")
            for field in ('ship_date', 'remark', 'inventory_site'):
                if value(field) is not None:
                    order[field] = value(field)
            order["items"].append({"item": product, "quantity": quantity,
                                   "configuration": value('configuration') or '',
                                   "inventory_site": value('inventory_site') or ''})
    return {"orders": grouped, "skipped": skipped, "messages": messages}


def read_google_sheet(credentials_path, sheet_name="Open Sales Order", worksheet_name=None):
    client = gspread.service_account(filename=credentials_path)
    spreadsheet = client.open(sheet_name)
    worksheet = spreadsheet.worksheet(worksheet_name) if worksheet_name else spreadsheet.get_worksheet(0)
    records = worksheet.get_all_records()
    df = pd.DataFrame(records, dtype=str)
    return _read_frames({worksheet.title: df})
