import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState } from "../components/Status";
import { TransactionTable } from "../components/TransactionTable";
import { ACCOUNT_TYPE_LABELS, formatMoney, labelFor } from "../lib/format";
import { useApiData } from "../lib/useApiData";

export function AccountDetailPage() {
  const { accountId = "" } = useParams();
  const result = useApiData(
    (signal) =>
      Promise.all([api.getAccount(accountId, signal), api.listTransactions({ account_id: accountId }, signal)]),
    [accountId],
  );

  if (result.status === "loading") return <LoadingState label="Loading account…" />;
  if (result.status === "error") {
    const notFound = result.error.status === 404;
    return (
      <ErrorState
        title={notFound ? "Account not found" : "We couldn't load this account"}
        error={result.error}
        onRetry={notFound ? undefined : result.reload}
      />
    );
  }

  const [account, transactions] = result.data;
  return (
    <>
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to="/dashboard">Dashboard</Link> <span aria-hidden="true">/</span> <span>{account.name}</span>
      </nav>
      <div className="page-header">
        <div>
          <p className="eyebrow">
            {labelFor(ACCOUNT_TYPE_LABELS, account.type)} · <span data-testid="account-number">{account.masked_number}</span>
          </p>
          <h1>{account.name}</h1>
        </div>
        <div className="balance-panel">
          <p className="balance-panel__label">Available balance</p>
          <p className="balance-panel__value" data-testid="account-balance">
            {formatMoney(account.balance, account.currency)} <span className="account-card__currency">{account.currency}</span>
          </p>
        </div>
      </div>
      <div className="button-row">
        <Link className="button button--primary" to={`/transfer?from=${encodeURIComponent(account.id)}`}>
          Transfer from this account
        </Link>
        <Link className="button button--secondary" to={`/transactions?account_id=${encodeURIComponent(account.id)}`}>
          Filter full history
        </Link>
      </div>

      <section aria-labelledby="history-heading" className="section">
        <h2 id="history-heading" className="section-title">
          Transaction history
        </h2>
        {transactions.length === 0 ? (
          <EmptyState title="No transactions yet">
            <p>Transactions for this account will appear here.</p>
          </EmptyState>
        ) : (
          <TransactionTable
            transactions={transactions}
            perspectiveAccountId={account.id}
            caption={`Transaction history for ${account.name}`}
          />
        )}
      </section>
    </>
  );
}
