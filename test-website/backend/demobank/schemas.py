from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


MONEY_PATTERN = re.compile(r"^-?(?:0|[1-9]\d{0,9})(?:\.\d{1,2})?$")


def money(value: Decimal) -> str:
    return f"{value:.2f}"


def utc_timestamp(value: datetime) -> str:
    value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Customer(StrictModel):
    id: UUID
    display_name: str
    masked_email: str


class Account(StrictModel):
    id: UUID
    name: str
    type: Literal["checking", "savings"]
    masked_number: str
    currency: str
    balance: str


class AccountSummary(StrictModel):
    id: UUID
    name: str
    masked_number: str


class AccountDelta(StrictModel):
    account_id: UUID
    amount_delta: str


class Transaction(StrictModel):
    id: UUID
    type: Literal["deposit", "withdrawal", "transfer"]
    status: Literal["completed", "pending", "failed"]
    amount: str
    memo: str | None
    occurred_at: str
    source_account: AccountSummary | None
    destination_account: AccountSummary | None
    account_deltas: list[AccountDelta]


class SessionResponse(StrictModel):
    customer: Customer


class AccountsResponse(StrictModel):
    accounts: list[Account]


class AccountResponse(StrictModel):
    account: Account


class TransactionsResponse(StrictModel):
    transactions: list[Transaction]


class TransferRequest(StrictModel):
    source_account_id: UUID
    destination_account_id: UUID
    amount: str
    memo: str | None = None

    @field_validator("amount")
    @classmethod
    def validate_amount_format(cls, value: str) -> str:
        if not isinstance(value, str) or not MONEY_PATTERN.fullmatch(value):
            raise ValueError("Amount must be a decimal string with no more than two decimal places.")
        return value


class TransferResponse(StrictModel):
    transaction: Transaction
    accounts: list[Account]


class HealthResponse(StrictModel):
    status: Literal["ok"]


class ErrorDetail(StrictModel):
    code: str
    message: str


class ErrorResponse(StrictModel):
    error: ErrorDetail
