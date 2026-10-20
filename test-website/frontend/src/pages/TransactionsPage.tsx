import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import {
  TRANSACTION_STATUSES,
  TRANSACTION_TYPES,
  type TransactionFilters,
  type TransactionStatus,
  type TransactionType,
} from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "../components/Status";
import { TransactionTable } from "../components/TransactionTable";
import { STATUS_LABELS, TYPE_LABELS, accountLabel } from "../lib/format";
import { useApiData } from "../lib/useApiData";

const FILTER_KEYS = ["account_id", "type", "status", "start_date", "end_date"] as const;
type FilterKey = (typeof FILTER_KEYS)[number];
type FilterForm = Record<FilterKey, string>;

const EMPTY_FILTERS: FilterForm = { account_id: "", type: "", status: "", start_date: "", end_date: "" };

function filtersFromParams(params: URLSearchParams): FilterForm {
  const form = { ...EMPTY_FILTERS };
  for (const key of FILTER_KEYS) form[key] = params.get(key) ?? "";
  return form;
}

function toApiFilters(form: FilterForm): TransactionFilters {
  return {
    account_id: form.account_id || undefined,
    type: (form.type || undefined) as TransactionType | undefined,
    status: (form.status || undefined) as TransactionStatus | undefined,
    // <input type="date"> values are already YYYY-MM-DD calendar dates.
    start_date: form.start_date || undefined,
    end_date: form.end_date || undefined,
  };
}

export function TransactionsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const applied = filtersFromParams(searchParams);
  const appliedKey = FILTER_KEYS.map((k) => applied[k]).join("|");
  const hasFilters = FILTER_KEYS.some((k) => applied[k] !== "");

  const [form, setForm] = useState<FilterForm>(applied);
  // Keep the form in sync when filters change via navigation (back/forward, links).
  useEffect(() => setForm(filtersFromParams(searchParams)), [appliedKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const accounts = useApiData((signal) => api.listAccounts(signal), []);
  const transactions = useApiData((signal) => api.listTransactions(toApiFilters(applied), signal), [appliedKey]);

  function update(key: FilterKey, value: string) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function applyFilters(event: FormEvent) {
    event.preventDefault();
    const next = new URLSearchParams();
    for (const key of FILTER_KEYS) if (form[key]) next.set(key, form[key]);
    setSearchParams(next);
  }

  function clearFilters() {
    setForm(EMPTY_FILTERS);
    setSearchParams(new URLSearchParams());
  }

  const accountOptions = accounts.status === "success" ? accounts.data : [];

  return (
    <>
      <div className="page-header">
        <div>
          <p className="eyebrow">History</p>
          <h1>Transactions</h1>
        </div>
      </div>

      <form className="panel filters" onSubmit={applyFilters} aria-label="Filter transactions">
        <div className="filters__grid">
          <div className="field">
            <label htmlFor="filter-account">Account</label>
            <select
              id="filter-account"
              value={form.account_id}
              onChange={(e) => update("account_id", e.target.value)}
            >
              <option value="">All accounts</option>
              {accountOptions.map((a) => (
                <option key={a.id} value={a.id}>
                  {accountLabel(a)}
                </option>
              ))}
              {/* Keep an applied account visible even before the account list loads. */}
              {form.account_id && !accountOptions.some((a) => a.id === form.account_id) && (
                <option value={form.account_id}>Selected account</option>
              )}
            </select>
          </div>
          <div className="field">
            <label htmlFor="filter-type">Type</label>
            <select id="filter-type" value={form.type} onChange={(e) => update("type", e.target.value)}>
              <option value="">All types</option>
              {TRANSACTION_TYPES.map((t) => (
                <option key={t} value={t}>
                  {TYPE_LABELS[t]}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="filter-status">Status</label>
            <select id="filter-status" value={form.status} onChange={(e) => update("status", e.target.value)}>
              <option value="">All statuses</option>
              {TRANSACTION_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABELS[s]}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="filter-start">Start date</label>
            <input
              id="filter-start"
              type="date"
              value={form.start_date}
              onChange={(e) => update("start_date", e.target.value)}
              aria-describedby="date-hint"
            />
          </div>
          <div className="field">
            <label htmlFor="filter-end">End date</label>
            <input
              id="filter-end"
              type="date"
              value={form.end_date}
              onChange={(e) => update("end_date", e.target.value)}
              aria-describedby="date-hint"
            />
          </div>
        </div>
        <p id="date-hint" className="hint">
          Start and end dates are inclusive and use Pacific Time.
        </p>
        {accounts.status === "error" && (
          <p className="hint hint--error" role="alert">
            Account choices could not be loaded: {accounts.error.message}
          </p>
        )}
        <div className="button-row">
          <button type="submit" className="button button--primary">
            Apply filters
          </button>
          <button type="button" className="button button--secondary" onClick={clearFilters}>
            Clear filters
          </button>
        </div>
      </form>

      <section aria-labelledby="results-heading" className="section">
        <h2 id="results-heading" className="section-title">
          {hasFilters ? "Filtered results" : "All transactions"}
        </h2>
        {transactions.status === "loading" && <LoadingState label="Loading transactions…" />}
        {transactions.status === "error" && (
          <ErrorState
            title="We couldn't load transactions"
            error={transactions.error}
            onRetry={transactions.reload}
          />
        )}
        {transactions.status === "success" &&
          (transactions.data.length === 0 ? (
            <EmptyState title={hasFilters ? "No transactions match these filters" : "No transactions yet"}>
              {hasFilters && (
                <>
                  <p>Try a different combination, or clear the filters to see all transactions.</p>
                  <button type="button" className="button button--secondary" onClick={clearFilters}>
                    Clear filters
                  </button>
                </>
              )}
            </EmptyState>
          ) : (
            <>
              <p className="result-count" role="status" data-testid="result-count">
                Showing {transactions.data.length} {transactions.data.length === 1 ? "transaction" : "transactions"}
              </p>
              <TransactionTable
                transactions={transactions.data}
                perspectiveAccountId={applied.account_id || undefined}
                caption={hasFilters ? "Filtered transaction history" : "All transactions"}
              />
            </>
          ))}
      </section>
    </>
  );
}
