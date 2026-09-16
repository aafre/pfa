import re


def normalize_description(description: str) -> str:
    return re.sub(r"\s+", " ", description.strip()).upper()


def merchant_from_description(description: str) -> str:
    s = description.strip()
    # Indian UPI format: UPI-[AUTOPAY-]<Merchant>-<VPA>-...
    if s.upper().startswith("UPI-"):
        parts = s.split("-")
        if len(parts) >= 3 and parts[1].strip().upper() == "AUTOPAY":
            return re.sub(r"\s+", " ", parts[2].strip()).strip(" -")[:240]
        if len(parts) >= 2:
            name = parts[1].strip()
            name = re.sub(r"(?i)\s+UPI$", "", name).strip()
            return re.sub(r"\s+", " ", name).strip(" -")[:240]

    # ACH format: ACH D- HDFC BANK LTD-472354631
    if s.upper().startswith("ACH "):
        m = re.match(r"(?i)ACH\s+[DR]-\s*([^-]+)", s)
        if m:
            return re.sub(r"\s+", " ", m.group(1).strip()).strip(" -")[:240]

    # NEFT format: NEFT CR-HSBC0560002-HSBC BANK PLC-...
    if s.upper().startswith("NEFT "):
        parts = s.split("-")
        if len(parts) >= 3:
            return re.sub(r"\s+", " ", parts[2].strip()).strip(" -")[:240]

    normalized = normalize_description(description)
    normalized = re.sub(r"\b(?:POS|CARD|REF|AUTH|TXN)\b", "", normalized)
    normalized = re.sub(r"\b\d{3,}\b", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip(" -")[:240]
