import { Link } from "react-router-dom";
import type { Account } from "../api/types";
import { ACCOUNT_TYPE_LABELS, formatMoney, labelFor } from "../lib/format";

export function AccountCard({ account }: { account: Account }) {
  return (
    <Link
      to={`/accounts/${encodeURIComponent(account.id)}`}
      className={`account-card account-card--${account.type}`}
      data-testid="account-card"
    >
      <div className="account-card__top">
        <h3 className="account-card__name">{account.name}</h3>
        <span className="badge">{labelFor(ACCOUNT_TYPE_LABELS, account.type)}</span>
      </div>
      <p className="account-card__number">
        <span className="visually-hidden">Account number ending </span>
        {account.masked_number}
      </p>
      <p className="account-card__label">Available balance</p>
      <p className="account-card__balance" data-testid="account-balance">
        {formatMoney(account.balance, account.currency)}
        <span className="account-card__currency">{account.currency}</span>
      </p>
      <span className="account-card__cta" aria-hidden="true">
        View details →
      </span>
    </Link>
  );
}
