import hashlib


def transaction_fingerprint(
    account: str,
    transaction_date: str,
    signed_amount_minor: int,
    currency: str,
    normalized_description: str,
    external_id: str | None = None,
    occurrence: int = 1,
) -> str:
    identity = external_id or f"{normalized_description}|{occurrence}"
    raw = "|".join(
        (account, transaction_date, str(signed_amount_minor), currency.upper(), identity)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
