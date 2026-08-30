from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from pfa.db.models import (
    AccountModel,
    TransactionModel,
    TransferEventModel,
    TransferLegModel,
    TransferMatchDecisionModel,
)
from pfa.db.unit_of_work import UnitOfWork
from pfa.domain.accounts import AccountType
from pfa.domain.transactions import (
    TransferLegRole,
    TransferMatchState,
    TransferPurpose,
    signed_minor,
)


@dataclass(frozen=True, slots=True)
class TransferMatchResult:
    accepted: int = 0
    suggested: int = 0


def _match_key(left_id: int, right_id: int) -> str:
    return hashlib.sha256(f"{left_id}:{right_id}".encode()).hexdigest()


def _is_card_payment(row: TransactionModel) -> bool:
    return (
        getattr(row, "kind", None) == "transfer"
        and getattr(row, "transfer_purpose", None) == TransferPurpose.CREDIT_CARD_PAYMENT.value
        and signed_minor(row.amount_minor, row.flow_direction) > 0
    )


def _owned_card(account: AccountModel) -> bool:
    return AccountType(account.account_type) == AccountType.CREDIT_CARD


def match_transfers(uow: UnitOfWork) -> TransferMatchResult:
    accounts = {account.id: account for account in uow.accounts.all()}
    rows = uow.transactions.all()
    linked = uow.transfers.linked_transaction_ids()
    cards = [
        row
        for row in rows
        if row.id not in linked
        and _is_card_payment(row)
        and row.account_id in accounts
        and _owned_card(accounts[row.account_id])
    ]
    banks = [
        row
        for row in rows
        if row.id not in linked
        and row.account_id in accounts
        and AccountType(accounts[row.account_id].account_type)
        in {AccountType.CURRENT, AccountType.SAVINGS}
        and signed_minor(row.amount_minor, row.flow_direction) < 0
        and "AMERICAN EXPRESS" in row.raw_description.upper()
    ]
    possible: list[tuple[TransactionModel, TransactionModel, bool, tuple[str, ...]]] = []
    for bank in banks:
        for card in cards:
            if bank.currency.upper() != card.currency.upper():
                continue
            if abs(signed_minor(bank.amount_minor, bank.flow_direction)) != abs(
                signed_minor(card.amount_minor, card.flow_direction)
            ):
                continue
            if abs((bank.transaction_date - card.transaction_date).days) > 3:
                continue
            card_account = accounts[card.account_id]
            institution = (card_account.institution or card_account.name).upper()
            strong = bool(bank.external_id and bank.external_id == card.external_id) or (
                "AMERICAN EXPRESS" in institution or "AMEX" in institution
            )
            reasons: tuple[str, ...] = (
                ("shared_reference",)
                if bank.external_id and bank.external_id == card.external_id
                else ("institution_cue",)
                if strong
                else ("amount_date_only",)
            )
            possible.append((bank, card, strong, reasons))

    counts: dict[int, int] = {}
    for bank, card, _, _ in possible:
        counts[bank.id] = counts.get(bank.id, 0) + 1
        counts[card.id] = counts.get(card.id, 0) + 1

    accepted = suggested = 0
    now = datetime.now(UTC).replace(tzinfo=None)
    for bank, card, strong, reasons in possible:
        key = _match_key(bank.id, card.id)
        if uow.transfers.decision(key) is not None:
            continue
        ambiguous = counts[bank.id] > 1 or counts[card.id] > 1
        accepted_match = strong and not ambiguous
        decision = TransferMatchDecisionModel(
            stable_match_key=key,
            left_transaction_id=bank.id,
            right_transaction_id=card.id,
            state=(
                TransferMatchState.ACCEPTED if accepted_match else TransferMatchState.SUGGESTED
            ).value,
            confidence=0.99 if accepted_match else 0.55,
            reason_codes_json=json.dumps(["ambiguous_amount_date"] if ambiguous else list(reasons)),
            created_at=now,
        )
        uow.transfers.add_decision(decision)
        if accepted_match:
            event = TransferEventModel(
                purpose=TransferPurpose.CREDIT_CARD_PAYMENT.value,
                match_method="automatic",
                created_at=now,
            )
            uow.transfers.add_event(
                event,
                [
                    TransferLegModel(
                        transaction_id=bank.id,
                        role=TransferLegRole.SOURCE.value,
                    ),
                    TransferLegModel(
                        transaction_id=card.id,
                        role=TransferLegRole.DESTINATION.value,
                    ),
                ],
            )
            decision.event_id = event.id
            bank.kind = "transfer"
            bank.transfer_purpose = TransferPurpose.CREDIT_CARD_PAYMENT.value
            bank.category = None
            bank.classification_source = "rule"
            bank.classification_reason = "paired owned credit-card repayment"
            linked.update((bank.id, card.id))
            accepted += 1
        else:
            suggested += 1
    return TransferMatchResult(accepted=accepted, suggested=suggested)
