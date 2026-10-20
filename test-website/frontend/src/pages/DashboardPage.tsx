import { Link } from "react-router-dom";
import { api } from "../api/client";
import { AccountCard } from "../components/AccountCard";
import { EmptyState, ErrorState, LoadingState } from "../components/Status";
import { useApiData } from "../lib/useApiData";

export function DashboardPage() {
  const result = useApiData(
    (signal) => Promise.all([api.getMe(signal), api.listAccounts(signal)]),
    [],
  );

  if (result.status === "loading") return <LoadingState label="Loading your accounts…" />;
  if (result.status === "error") {
    return <ErrorState title="We couldn't load your accounts" error={result.error} onRetry={result.reload} backTo={null} />;
  }

  const [me, accounts] = result.data;
  return (
    <>
      <div className="page-header">
        <div>
          <p className="eyebrow">Dashboard</p>
          <h1>Welcome, {me.display_name}</h1>
        </div>
        <div className="button-row">
          <Link className="button button--primary" to="/transfer">
            Transfer funds
          </Link>
          <Link className="button button--secondary" to="/transactions">
            View all transactions
          </Link>
        </div>
      </div>

      <section aria-labelledby="accounts-heading">
        <h2 id="accounts-heading" className="section-title">
          Your accounts
        </h2>
        {accounts.length === 0 ? (
          <EmptyState title="No accounts found">
            <p>This demo customer has no accounts to show.</p>
          </EmptyState>
        ) : (
          <div className="account-grid">
            {accounts.map((account) => (
              <AccountCard key={account.id} account={account} />
            ))}
          </div>
        )}
      </section>
    </>
  );
}
