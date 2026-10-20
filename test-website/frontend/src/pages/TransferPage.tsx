import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { Account, TransferResult } from "../api/types";
import { Alert, ErrorState, LoadingState } from "../components/Status";
import { STATUS_LABELS, accountLabel, formatDateTime, formatMoney, labelFor } from "../lib/format";
import { useApiData } from "../lib/useApiData";

interface FormState {
  source_account_id: string;
  destination_account_id: string;
  amount: string;
  memo: string;
}

type FieldErrors = Partial<Record<keyof FormState, string>>;

function requiredFieldErrors(form: FormState): FieldErrors {
  const errors: FieldErrors = {};
  if (!form.source_account_id) errors.source_account_id = "Choose the account to transfer from.";
  if (!form.destination_account_id) errors.destination_account_id = "Choose the account to transfer to.";
  if (!form.amount.trim()) errors.amount = "Enter an amount to transfer.";
  return errors;
}

/** Puts the API's message next to the field it most likely concerns, in addition to the summary. */
function fieldForErrorCode(code: string): keyof FormState | null {
  if (/amount|funds/i.test(code)) return "amount";
  if (/same|destination/i.test(code)) return "destination_account_id";
  if (/source/i.test(code)) return "source_account_id";
  return null;
}

export function TransferPage() {
  const [searchParams] = useSearchParams();
  const preferredSource = searchParams.get("from") ?? "";
  const loaded = useApiData((signal) => api.listAccounts(signal), []);

  const [accounts, setAccounts] = useState<Account[]>([]);
  const [form, setForm] = useState<FormState>({
    source_account_id: "",
    destination_account_id: "",
    amount: "",
    memo: "",
  });
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<ApiError | null>(null);
  const [result, setResult] = useState<TransferResult | null>(null);
  const feedbackRef = useRef<HTMLDivElement>(null);

  // Seed local account state and default selections once the account list arrives.
  useEffect(() => {
    if (loaded.status !== "success") return;
    const list = loaded.data;
    setAccounts(list);
    setForm((prev) => {
      const source = list.some((a) => a.id === preferredSource) ? preferredSource : list[0]?.id ?? "";
      const destination = list.find((a) => a.id !== source)?.id ?? "";
      return { ...prev, source_account_id: source, destination_account_id: destination };
    });
  }, [loaded.status, loaded.data, preferredSource]);

  useEffect(() => {
    if ((result || submitError) && feedbackRef.current) feedbackRef.current.focus();
  }, [result, submitError]);

  function update(key: keyof FormState, value: string) {
    setForm((prev) => ({ ...prev, [key]: value }));
    setFieldErrors((prev) => ({ ...prev, [key]: undefined }));
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setResult(null);
    setSubmitError(null);

    const errors = requiredFieldErrors(form);
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    setSubmitting(true);
    try {
      const response = await api.createTransfer({
        source_account_id: form.source_account_id,
        destination_account_id: form.destination_account_id,
        amount: form.amount.trim(),
        memo: form.memo.trim(),
      });
      // Show the balances the backend returned; never compute them locally.
      setAccounts((prev) => prev.map((a) => response.accounts.find((u) => u.id === a.id) ?? a));
      setResult(response);
      setForm((prev) => ({ ...prev, amount: "", memo: "" }));
    } catch (err) {
      const apiError =
        err instanceof ApiError ? err : new ApiError(0, "unexpected", "The transfer could not be submitted.");
      setSubmitError(apiError);
      const field = fieldForErrorCode(apiError.code);
      if (field) setFieldErrors({ [field]: apiError.message });
    } finally {
      setSubmitting(false);
    }
  }

  if (loaded.status === "loading") return <LoadingState label="Loading your accounts…" />;
  if (loaded.status === "error") {
    return <ErrorState title="We couldn't load your accounts" error={loaded.error} onRetry={loaded.reload} />;
  }

  const byId = new Map(accounts.map((a) => [a.id, a]));
  const describedBy = (key: keyof FormState, hint?: string) =>
    [hint, fieldErrors[key] ? `${key}-error` : undefined].filter(Boolean).join(" ") || undefined;

  return (
    <>
      <div className="page-header">
        <div>
          <p className="eyebrow">Move money</p>
          <h1>Transfer between your accounts</h1>
        </div>
      </div>

      <div className="transfer-layout">
        <form className="panel transfer-form" onSubmit={handleSubmit} noValidate aria-busy={submitting}>
          <div className="field">
            <label htmlFor="source">From account</label>
            <select
              id="source"
              name="source_account_id"
              value={form.source_account_id}
              onChange={(e) => update("source_account_id", e.target.value)}
              aria-invalid={Boolean(fieldErrors.source_account_id)}
              aria-describedby={describedBy("source_account_id")}
            >
              <option value="">Select an account</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {accountLabel(a)} — {formatMoney(a.balance, a.currency)}
                </option>
              ))}
            </select>
            {fieldErrors.source_account_id && (
              <p id="source_account_id-error" className="field__error">
                {fieldErrors.source_account_id}
              </p>
            )}
          </div>

          <div className="field">
            <label htmlFor="destination">To account</label>
            <select
              id="destination"
              name="destination_account_id"
              value={form.destination_account_id}
              onChange={(e) => update("destination_account_id", e.target.value)}
              aria-invalid={Boolean(fieldErrors.destination_account_id)}
              aria-describedby={describedBy("destination_account_id")}
            >
              <option value="">Select an account</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {accountLabel(a)} — {formatMoney(a.balance, a.currency)}
                </option>
              ))}
            </select>
            {fieldErrors.destination_account_id && (
              <p id="destination_account_id-error" className="field__error">
                {fieldErrors.destination_account_id}
              </p>
            )}
          </div>

          <div className="field">
            <label htmlFor="amount">Amount (USD)</label>
            <div className="input-affix">
              <span className="input-affix__prefix" aria-hidden="true">
                $
              </span>
              <input
                id="amount"
                name="amount"
                type="text"
                inputMode="decimal"
                autoComplete="off"
                placeholder="0.00"
                value={form.amount}
                onChange={(e) => update("amount", e.target.value)}
                aria-invalid={Boolean(fieldErrors.amount)}
                aria-describedby={describedBy("amount", "amount-hint")}
              />
            </div>
            <p id="amount-hint" className="hint">
              Up to two decimal places, for example 25.00.
            </p>
            {fieldErrors.amount && (
              <p id="amount-error" className="field__error">
                {fieldErrors.amount}
              </p>
            )}
          </div>

          <div className="field">
            <label htmlFor="memo">
              Memo <span className="optional">(optional)</span>
            </label>
            <input
              id="memo"
              name="memo"
              type="text"
              autoComplete="off"
              value={form.memo}
              onChange={(e) => update("memo", e.target.value)}
            />
          </div>

          <button type="submit" className="button button--primary button--block" disabled={submitting}>
            {submitting ? "Submitting transfer…" : "Transfer funds"}
          </button>
        </form>

        <div className="transfer-side">
          <div ref={feedbackRef} tabIndex={-1} className="feedback-anchor">
            {submitting && <LoadingState label="Submitting transfer…" />}
            {submitError && (
              <Alert tone="error" title="Transfer not completed">
                <p>{submitError.message}</p>
                <p className="muted">No money was moved. Your balances are unchanged.</p>
              </Alert>
            )}
            {result && <TransferReceipt result={result} byId={byId} />}
          </div>

          <section className="panel" aria-labelledby="balances-heading">
            <h2 id="balances-heading" className="section-title">
              Current balances
            </h2>
            <ul className="balance-list">
              {accounts.map((a) => (
                <li key={a.id}>
                  <Link to={`/accounts/${encodeURIComponent(a.id)}`}>{accountLabel(a)}</Link>
                  <span className="balance-list__value" data-testid="transfer-balance">
                    {formatMoney(a.balance, a.currency)}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </div>
    </>
  );
}

function TransferReceipt({ result, byId }: { result: TransferResult; byId: Map<string, Account> }) {
  const tx = result.transaction;
  const completed = tx.status === "completed";
  const source = tx.source_account;
  const destination = tx.destination_account;
  return (
    <Alert
      tone={completed ? "success" : "info"}
      title={completed ? "Transfer complete" : `Transfer recorded with status: ${labelFor(STATUS_LABELS, tx.status)}`}
    >
      <dl className="receipt">
        <div>
          <dt>Amount</dt>
          <dd>{formatMoney(tx.amount)}</dd>
        </div>
        <div>
          <dt>From</dt>
          <dd>{source ? accountLabel(source) : "—"}</dd>
        </div>
        <div>
          <dt>To</dt>
          <dd>{destination ? accountLabel(destination) : "—"}</dd>
        </div>
        <div>
          <dt>Date &amp; time</dt>
          <dd>{formatDateTime(tx.occurred_at)}</dd>
        </div>
        {tx.memo && (
          <div>
            <dt>Memo</dt>
            <dd>{tx.memo}</dd>
          </div>
        )}
        {result.accounts.map((a) => (
          <div key={a.id}>
            <dt>New {(byId.get(a.id) ?? a).name} balance</dt>
            <dd>{formatMoney(a.balance, a.currency)}</dd>
          </div>
        ))}
      </dl>
      <div className="button-row">
        {source && (
          <Link className="button button--ghost button--small" to={`/accounts/${encodeURIComponent(source.id)}`}>
            View {source.name} history
          </Link>
        )}
        {destination && (
          <Link className="button button--ghost button--small" to={`/accounts/${encodeURIComponent(destination.id)}`}>
            View {destination.name} history
          </Link>
        )}
      </div>
    </Alert>
  );
}
