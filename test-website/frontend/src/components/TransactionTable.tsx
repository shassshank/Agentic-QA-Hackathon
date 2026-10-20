import type { Transaction } from "../api/types";
import {
  STATUS_LABELS,
  TYPE_LABELS,
  accountLabel,
  decimalSign,
  formatDateTime,
  formatMoney,
  labelFor,
  transactionDescription,
} from "../lib/format";

interface AmountView {
  text: string;
  direction: "credit" | "debit" | "neutral";
  srLabel: string;
}

/**
 * Amount direction comes from the backend's account_deltas. With an account perspective we show that
 * account's delta; otherwise single-account transactions use their own delta and transfers stay neutral.
 */
function amountFor(tx: Transaction, perspectiveAccountId?: string): AmountView {
  const delta =
    (perspectiveAccountId && tx.account_deltas.find((d) => d.account_id === perspectiveAccountId)) ||
    (tx.type !== "transfer" && tx.account_deltas.length === 1 ? tx.account_deltas[0] : undefined);

  if (delta) {
    const sign = decimalSign(delta.amount_delta);
    const direction = sign > 0 ? "credit" : sign < 0 ? "debit" : "neutral";
    return {
      text: formatMoney(delta.amount_delta, "USD", true),
      direction,
      srLabel: direction === "credit" ? "Money in" : direction === "debit" ? "Money out" : "No change",
    };
  }
  return { text: formatMoney(tx.amount), direction: "neutral", srLabel: "Moved between accounts" };
}

function AccountCell({ tx }: { tx: Transaction }) {
  if (tx.type === "transfer") {
    return (
      <span className="tx-accounts">
        <span>
          <span className="tx-accounts__label">From </span>
          {tx.source_account ? accountLabel(tx.source_account) : "—"}
        </span>
        <span>
          <span className="tx-accounts__label">To </span>
          {tx.destination_account ? accountLabel(tx.destination_account) : "—"}
        </span>
      </span>
    );
  }
  const account = tx.type === "deposit" ? tx.destination_account ?? tx.source_account : tx.source_account ?? tx.destination_account;
  return <span>{account ? accountLabel(account) : "—"}</span>;
}

export function TransactionTable({
  transactions,
  perspectiveAccountId,
  caption,
}: {
  transactions: Transaction[];
  perspectiveAccountId?: string;
  caption: string;
}) {
  return (
    <div className="table-wrap">
      <table className="tx-table" data-testid="transaction-table">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Date &amp; time</th>
            <th scope="col">Description</th>
            <th scope="col">Type</th>
            <th scope="col">Account</th>
            <th scope="col" className="num">Amount</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {transactions.map((tx) => {
            const amount = amountFor(tx, perspectiveAccountId);
            return (
              <tr key={tx.id} data-testid="transaction-row" data-transaction-id={tx.id}>
                <td data-label="Date & time">
                  <time dateTime={tx.occurred_at}>{formatDateTime(tx.occurred_at)}</time>
                </td>
                <td data-label="Description">{transactionDescription(tx)}</td>
                <td data-label="Type">{labelFor(TYPE_LABELS, tx.type)}</td>
                <td data-label="Account">
                  <AccountCell tx={tx} />
                </td>
                <td data-label="Amount" className={`num amount amount--${amount.direction}`}>
                  <span className="visually-hidden">{amount.srLabel}: </span>
                  {amount.text}
                </td>
                <td data-label="Status">
                  <span className={`status status--${tx.status}`}>{labelFor(STATUS_LABELS, tx.status)}</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
