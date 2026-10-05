"""Labor helpers adapted from playoung2818/MRP_System Webpage/server.py.
Upstream commit f37fe90861187b8806cd6ac6fb3ea3fbb2694578.
Kept independent of Flask, cached globals, and database writes.
"""
import pandas as pd
import numpy as np

WEEKLY_LABOR_CAPACITY_HOURS = 90.0
LABOR_HOURS_PER_UNIT = {"NUVO": 1.0, "POC": 0.5, "NRU": 1.0, "SEMIL": 1.0, "F": 1.0}
LABOR_FAMILY_LABELS = {"NUVO": "Nuvo", "POC": "POC", "NRU": "NRU", "SEMIL": "SEMIL", "F": "F"}

def _is_unassigned_lt_series(s: pd.Series) -> pd.Series:
    dates = pd.to_datetime(s, errors="coerce")
    return (dates.dt.month.eq(7) & dates.dt.day.eq(4)) | (dates.dt.month.eq(12) & dates.dt.day.eq(31))

def _format_num(value, digits: int = 1) -> str:
    try:
        num = float(value)
    except Exception:
        return ""
    if not np.isfinite(num):
        return ""
    if abs(num - round(num)) < 0.000001:
        return str(int(round(num)))
    return f"{num:.{digits}f}".rstrip("0").rstrip(".")

def _parse_float(value, default: float | None = None) -> float | None:
    try:
        num = float(value)
    except Exception:
        return default
    if not np.isfinite(num):
        return default
    return num

def _classify_labor_family(item: object) -> tuple[str, float | None]:
    item_upper = str(item or "").strip().upper()
    if item_upper.startswith("NUVO-"):
        return LABOR_FAMILY_LABELS["NUVO"], LABOR_HOURS_PER_UNIT["NUVO"]
    if item_upper.startswith("NRU-"):
        return LABOR_FAMILY_LABELS["NRU"], LABOR_HOURS_PER_UNIT["NRU"]
    if item_upper.startswith("SEMIL-"):
        return LABOR_FAMILY_LABELS["SEMIL"], LABOR_HOURS_PER_UNIT["SEMIL"]
    if item_upper.startswith("POC-"):
        return LABOR_FAMILY_LABELS["POC"], LABOR_HOURS_PER_UNIT["POC"]
    if item_upper.startswith("F") and not item_upper.startswith(("FK", "FPNL-", "FANKIT", "FAN-")):
        return LABOR_FAMILY_LABELS["F"], LABOR_HOURS_PER_UNIT["F"]
    return "Unknown", None

def _build_first_wo_item_map(structured_df: pd.DataFrame | None) -> dict[str, dict[str, object]]:
    if (
        structured_df is None
        or structured_df.empty
        or "QB Num" not in structured_df.columns
        or "Item" not in structured_df.columns
    ):
        return {}

    work = structured_df.copy()
    work["QB Num"] = work["QB Num"].astype(str).str.strip()
    work = work.loc[work["QB Num"].ne("")]
    if work.empty:
        return {}

    # SO_INV (wo_structured) uses "Qty(-)"/"Name"; FINAL_SO (open_sales_orders, rebuilt for
    # Production Planning) uses "Qty"/"Customer" instead. Accept either caller's schema.
    qty_col = "Qty(-)" if "Qty(-)" in work.columns else ("Qty" if "Qty" in work.columns else None)
    first_map: dict[str, dict[str, object]] = {}
    for qb_num, group in work.groupby("QB Num", sort=False):
        unit_rows: list[dict[str, object]] = []
        for pos, (_, row) in enumerate(group.iterrows()):
            item = row.get("Item") or ""
            family, hours_per_unit = _classify_labor_family(item)
            if hours_per_unit is None:
                continue
            qty = 0.0
            if qty_col is not None:
                qty = _parse_float(row.get(qty_col), 0.0) or 0.0
            unit_rows.append(
                {
                    "item": item,
                    "qty": max(qty, 0.0),
                    "family": family,
                    "hours_per_unit": hours_per_unit,
                    "position": pos,
                }
            )

        if unit_rows:
            primary = unit_rows[0]
            total_qty = sum(float(r.get("qty") or 0.0) for r in unit_rows)
            base_labor_hours = sum(
                float(r.get("qty") or 0.0) * float(r.get("hours_per_unit") or 0.0)
                for r in unit_rows
            )
            first_map[str(qb_num).strip()] = {
                "item": primary.get("item") or "",
                "qty": total_qty,
                "unit_rows": unit_rows,
                "base_labor_hours": base_labor_hours,
            }
            continue

        first = group.iloc[0]
        qty = None
        if qty_col is not None:
            qty = _parse_float(first.get(qty_col))
        first_map[str(qb_num).strip()] = {
            "item": first.get("Item") or "",
            "qty": qty,
            "unit_rows": [],
            "base_labor_hours": None,
        }
    return first_map

def _picked_qty_for_wo(
    qb_num: object,
    total_units: float,
    wo_status: str,
    picked_qty_overrides: dict[str, float],
) -> tuple[float, bool]:
    key = str(qb_num or "").strip()
    if key in picked_qty_overrides:
        picked_qty = picked_qty_overrides[key]
        return max(picked_qty, 0.0), True
    if str(wo_status or "").strip().lower() == "picked":
        return max(total_units, 0.0), False
    return 0.0, False

def _build_production_order_row(
    qb_num: object,
    so_group: pd.DataFrame,
    *,
    labor_item_map: dict[str, dict[str, object]],
    wo_status_map: dict[str, str],
    picked_qty_overrides: dict[str, float],
    production_schedule_overrides: dict[str, str],
    production_date_str: str,
) -> dict:
    first = so_group.iloc[0]
    qb_key = str(qb_num).strip()
    customer = first.get("Customer") or first.get("Name") or ""
    terms = str(first.get("Terms") or "").strip()
    qty_val = first.get("Qty")
    try:
        qty_float = float(qty_val)
        qty_str = str(int(qty_float)) if qty_float.is_integer() else str(qty_float)
    except Exception:
        qty_str = str(qty_val) if qty_val is not None else ""
        qty_float = _parse_float(qty_val, 0.0) or 0.0

    labor_info = labor_item_map.get(qb_key, {})
    labor_qty = _parse_float(labor_info.get("qty"))
    if labor_qty is not None:
        qty_float = labor_qty
        qty_str = _format_num(labor_qty)

    item_name = labor_info.get("item") or first.get("Item") or ""
    unit_rows = labor_info.get("unit_rows") or []
    wo_status = wo_status_map.get(qb_key, "NA")
    picked_qty, picked_qty_saved = _picked_qty_for_wo(
        qb_num,
        qty_float,
        wo_status,
        picked_qty_overrides,
    )
    remaining_units = max(qty_float - picked_qty, 0.0)
    remaining_ratio = 0.0 if qty_float <= 0 else min(max(remaining_units / qty_float, 0.0), 1.0)
    base_labor_hours = _parse_float(labor_info.get("base_labor_hours"))
    if base_labor_hours is not None:
        labor_hours = base_labor_hours * remaining_ratio
    else:
        _, hours_per_unit = _classify_labor_family(item_name)
        labor_hours = None if hours_per_unit is None else remaining_units * hours_per_unit

    family_units_detail = {label: 0.0 for label in LABOR_FAMILY_LABELS.values()}
    if unit_rows:
        for unit_row in unit_rows:
            detail_family = unit_row.get("family")
            if detail_family in family_units_detail:
                family_units_detail[str(detail_family)] += float(unit_row.get("qty") or 0.0) * remaining_ratio
    else:
        family, _ = _classify_labor_family(item_name)
        if family in family_units_detail:
            family_units_detail[family] += remaining_units

    po_num = first.get("Customer PO") or first.get("P. O. #") or ""
    ship_date = pd.to_datetime(first.get("Lead Time"), errors="coerce")
    production_date = pd.to_datetime(production_date_str, errors="coerce")
    lt_matches_production_date = (
        pd.notnull(ship_date)
        and pd.notnull(production_date)
        and ship_date.normalize() <= production_date.normalize()
    )
    line = f"{item_name} x {qty_str}".strip()
    return {
        "qb_num": str(qb_num),
        "terms": terms,
        "customer": customer,
        "line": line,
        "qty": qty_float,
        "qty_str": qty_str,
        "remaining_units": remaining_units,
        "remaining_units_str": _format_num(remaining_units),
        "labor_hours": labor_hours,
        "labor_hours_str": _format_num(labor_hours) if labor_hours is not None else "Review",
        "family_units_detail": family_units_detail,
        "ship_date": ship_date.strftime("%Y-%m-%d") if pd.notnull(ship_date) else "",
        "production_date": production_date_str,
        "lt_matches_production_date": lt_matches_production_date,
        "production_date_saved": qb_key in production_schedule_overrides,
        "wo_status": wo_status,
        "picked_qty": picked_qty,
        "picked_qty_str": _format_num(picked_qty),
        "picked_qty_saved": picked_qty_saved,
        "pdf_url": "",
    }

def _summarize_labor_rows(rows: list[dict]) -> dict:
    known_hours = 0.0
    unknown_count = 0
    family_units = {label: 0.0 for label in LABOR_FAMILY_LABELS.values()}
    for row in rows:
        labor_hours = _parse_float(row.get("labor_hours"))
        if labor_hours is None:
            unknown_count += 1
        else:
            known_hours += labor_hours
        detail = row.get("family_units_detail") or {}
        for family, units in detail.items():
            if family in family_units:
                family_units[family] += float(units or 0)

    return {
        "known_hours": known_hours,
        "known_hours_str": _format_num(known_hours),
        "unknown_count": unknown_count,
        "family_counts": [
            {"label": label, "units_str": _format_num(family_units[label])}
            for label in ("POC", "Nuvo", "SEMIL", "NRU", "F")
        ],
    }

def _reorder_df_out_by_output(output_df: pd.DataFrame, df_out: pd.DataFrame) -> pd.DataFrame:
    """
    Reorder df_out to match the line ordering found in output_df.
    Both frames are expected to use columns: ['QB Num', 'Item'].
    """
    if output_df is None or output_df.empty:
        return df_out.sort_values(["QB Num", "Item"]).reset_index(drop=True)

    ref = output_df.copy()
    ref["__pos_out"] = ref.groupby("QB Num").cumcount()
    ref["__occ"] = ref.groupby(["QB Num", "Item"]).cumcount()
    ref_key = ref[["QB Num", "Item", "__occ", "__pos_out"]]

    tgt = df_out.copy()
    tgt["__occ"] = tgt.groupby(["QB Num", "Item"]).cumcount()

    merged = tgt.merge(ref_key, on=["QB Num", "Item", "__occ"], how="left")
    merged["__fallback"] = merged.groupby("QB Num").cumcount()
    merged["__pos_out"] = merged["__pos_out"].fillna(float("inf"))

    ordered = (
        merged.sort_values(["QB Num", "__pos_out", "__fallback"])
        .drop(columns=["__occ", "__pos_out", "__fallback"])
        .reset_index(drop=True)
    )
    return ordered

