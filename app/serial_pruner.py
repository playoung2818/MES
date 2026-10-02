import re


def extract_useful_number(sn: str) -> str:
    """
    Extract the useful part after SN/SNO-style barcode text.
    Based on https://github.com/playoung2818/Serial-Number-Prune

    Example: "Pcie-poe, SN0023783" -> "0023783"
    A bare leading S is part of the serial, not a prefix (e.g. S75CNS0L518419).
    """
    sn = sn.strip()
    match = re.search(
        r"(?<![A-Za-z0-9])(?:S/N|SNO(?=\s|[:#=])|SN)\s*[:#=]?\s*([A-Za-z0-9][A-Za-z0-9._/-]*)",
        sn, re.IGNORECASE,
    )
    return match.group(1) if match else sn


def _is_na(value: str) -> bool:
    normalized = re.sub(r"[^A-Za-z0-9]", "", value or "").upper()
    return normalized in {"NA", "NAN", "NONE", "NULL"}


def normalize_numeric_serial(value: str) -> str:
    """Strip numeric leading zeros without losing precision or changing letters."""
    return (value.lstrip('0') or '0') if re.fullmatch(r'[0-9]+', value) else value


def prune_and_sort_serials(raw_input: str, preserve_order: bool = False) -> list[str]:
    raw_list = re.split(r"[,\n;]+", raw_input or "")
    cleaned = [extract_useful_number(s.strip()) for s in raw_list]
    # Excel-style numeric normalization at any length, without converting to a
    # number (which could lose precision). All-zero serials normalize to "0".
    cleaned = [normalize_numeric_serial(value) for value in cleaned]
    cleaned = ["NA" if value.upper() in {"NA", "N/A"} else value
               for value in cleaned
               if value and (value.upper() in {"NA", "N/A"} or not _is_na(value))]

    if preserve_order:
        seen = set()
        return [s for s in cleaned if not (s in seen or seen.add(s))]
    return sorted(set(cleaned))
