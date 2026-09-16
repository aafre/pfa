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
from pfa.domain.errors import BatchError
from pfa.domain.transactions import (
    TransactionKind,
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
        row.kind == TransactionKind.TRANSFER.value
        and row.transfer_purpose == TransferPurpose.CREDIT_CARD_PAYMENT.value
        and signed_minor(row.amount_minor, row.flow_direction) > 0
    )


def _owned_card(account: AccountModel) -> bool:
    return AccountType(account.account_type) == AccountType.CREDIT_CARD


def _event_for_pair(
    uow: UnitOfWork,
    source: TransactionModel,
    destination: TransactionModel,
    *,
    method: str,
) -> TransferEventModel:
    now = datetime.now(UTC).replace(tzinfo=None)
    event = TransferEventModel(
        purpose=TransferPurpose.CREDIT_CARD_PAYMENT.value,
        match_method=method,
        created_at=now,
    )
    uow.transfers.add_event(
        event,
        [
            TransferLegModel(transaction_id=source.id, role=TransferLegRole.SOURCE.value),
            TransferLegModel(
                transaction_id=destination.id,
                role=TransferLegRole.DESTINATION.value,
            ),
        ],
    )
    return event


def _mark_bank_payment(bank: TransactionModel) -> None:
    bank.kind = TransactionKind.TRANSFER.value
    bank.transfer_purpose = TransferPurpose.CREDIT_CARD_PAYMENT.value
    bank.category = None
    bank.classification_source = "rule"
    bank.classification_reason = "paired owned credit-card repayment"


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
            event = _event_for_pair(uow, bank, card, method="automatic")
            decision.event_id = event.id
            _mark_bank_payment(bank)
            linked.update((bank.id, card.id))
            accepted += 1
        else:
            suggested += 1
    return TransferMatchResult(accepted=accepted, suggested=suggested)


def accept_suggestion(uow: UnitOfWork, decision_id: int) -> TransferEventModel:
    decision = uow.session.get(TransferMatchDecisionModel, decision_id)
    if decision is None or decision.state != TransferMatchState.SUGGESTED.value:
        raise BatchError(
            "TRANSFER_DECISION_INVALID", "transfer suggestion is no longer reviewable", 409
        )
    source = uow.session.get(TransactionModel, decision.left_transaction_id)
    destination = uow.session.get(TransactionModel, decision.right_transaction_id)
    if source is None or destination is None:
        raise BatchError(
            "TRANSFER_TRANSACTION_NOT_FOUND", "transfer transaction no longer exists", 422
        )
    event = create_manual_link(
        uow,
        [
            (source.id, TransferLegRole.SOURCE.value),
            (destination.id, TransferLegRole.DESTINATION.value),
        ],
        TransferPurpose.CREDIT_CARD_PAYMENT.value,
    )
    decision.state = TransferMatchState.ACCEPTED.value
    decision.event_id = event.id
    decision.reviewed_at = datetime.now(UTC).replace(tzinfo=None)
    _mark_bank_payment(source)
    return event


def dismiss_suggestion(uow: UnitOfWork, decision_id: int) -> TransferMatchDecisionModel:
    decision = uow.session.get(TransferMatchDecisionModel, decision_id)
    if decision is None or decision.state != TransferMatchState.SUGGESTED.value:
        raise BatchError(
            "TRANSFER_DECISION_INVALID", "transfer suggestion is no longer reviewable", 409
        )
    decision.state = TransferMatchState.DISMISSED.value
    decision.reviewed_at = datetime.now(UTC).replace(tzinfo=None)
    return decision


def create_manual_link(
    uow: UnitOfWork,
    legs: list[tuple[int, str]],
    purpose: str = TransferPurpose.OTHER.value,
) -> TransferEventModel:
    if len(legs) < 2 or sum(role == TransferLegRole.SOURCE.value for _, role in legs) != 1:
        raise BatchError(
            "TRANSFER_ROLES_INVALID",
            "a transfer needs exactly one source and two or more legs",
            422,
        )
    if sum(role == TransferLegRole.DESTINATION.value for _, role in legs) != 1:
        raise BatchError("TRANSFER_ROLES_INVALID", "a transfer needs exactly one destination", 422)
    if any(role not in {item.value for item in TransferLegRole} for _, role in legs):
        raise BatchError("TRANSFER_ROLES_INVALID", "unknown transfer leg role", 422)
    ids = [transaction_id for transaction_id, _ in legs]
    if len(set(ids)) != len(ids):
        raise BatchError("TRANSFER_LEGS_DUPLICATE", "a transaction can appear only once", 422)
    transactions = {row.id: row for row in uow.transactions.by_ids(ids)}
    if len(transactions) != len(ids):
        raise BatchError(
            "TRANSFER_TRANSACTION_NOT_FOUND", "one or more transactions do not exist", 422
        )
    linked = uow.transfers.linked_transaction_ids()
    if linked.intersection(ids):
        raise BatchError(
            "TRANSFER_ALREADY_LINKED", "one or more transactions are already linked", 409
        )
    account_ids = {transactions[transaction_id].account_id for transaction_id in ids}
    if len(account_ids) < 2:
        raise BatchError(
            "TRANSFER_SAME_ACCOUNT", "transfer legs must belong to different accounts", 422
        )
    accounts = {account.id: account for account in uow.accounts.all()}
    if any(not accounts[account_id].active for account_id in account_ids if account_id in accounts):
        raise BatchError("TRANSFER_ACCOUNT_INACTIVE", "all transfer accounts must be active", 422)
    source_id = next(transaction_id for transaction_id, role in legs if role == "source")
    destination_id = next(transaction_id for transaction_id, role in legs if role == "destination")
    if (
        signed_minor(transactions[source_id].amount_minor, transactions[source_id].flow_direction)
        >= 0
    ):
        raise BatchError("TRANSFER_SIGN_INVALID", "the source leg must be money out", 422)
    if (
        signed_minor(
            transactions[destination_id].amount_minor, transactions[destination_id].flow_direction
        )
        <= 0
    ):
        raise BatchError("TRANSFER_SIGN_INVALID", "the destination leg must be money in", 422)
    event = TransferEventModel(purpose=purpose, match_method="manual")
    for transaction_id, _role in legs:
        transaction = transactions[transaction_id]
        transaction.kind = TransactionKind.TRANSFER.value
        transaction.transfer_purpose = purpose
        transaction.category = None
        transaction.classification_source = "user"
        transaction.classification_reason = "explicit transfer link"
    uow.transfers.add_event(
        event,
        [
            TransferLegModel(transaction_id=transaction_id, role=role)
            for transaction_id, role in legs
        ],
    )
    return event
