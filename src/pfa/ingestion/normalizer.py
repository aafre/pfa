import re


def normalize_description(description: str) -> str:
    return re.sub(r"\s+", " ", description.strip()).upper()


def merchant_from_description(description: str) -> str:
    normalized = normalize_description(description)
    normalized = re.sub(r"\b(?:POS|CARD|REF|AUTH|TXN)\b", "", normalized)
    normalized = re.sub(r"\b\d{3,}\b", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip(" -")[:240]
