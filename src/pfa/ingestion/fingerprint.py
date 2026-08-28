import hashlib


def transaction_fingerprint(
    account: str,
    transaction_date: str,
    amount_minor: int,
    currency: str,
    normalized_description: str,
    external_id: str | None = None,
) -> str:
    identity = external_id or normalized_description
    raw = "|".join((account, transaction_date, str(amount_minor), currency.upper(), identity))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
