import type { Account, AccountSummary, Transaction, TransactionStatus, TransactionType } from "../api/types";

/** Calendar dates in the API's date filters are interpreted in this timezone, so we display times in it too. */
export const DISPLAY_TIME_ZONE = "America/Los_Angeles";

const DECIMAL_RE = /^([+-])?(\d+)(?:\.(\d+))?$/;

function currencyPrefix(currency: string): string {
  return currency.toUpperCase() === "USD" ? "$" : `${currency.toUpperCase()} `;
}

function groupThousands(digits: string): string {
  return digits.replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/**
 * Formats an API decimal string as money without converting it to a float.
 * `signed` adds an explicit "+" for positive values (used for per-account deltas).
 */
export function formatMoney(value: string, currency = "USD", signed = false): string {
  const match = DECIMAL_RE.exec(value.trim());
  if (!match) return value;
  const [, sign, whole, fraction = ""] = match;
  const frac = fraction.length >= 2 ? fraction : fraction.padEnd(2, "0");
  const isZero = /^0*$/.test(whole) && /^0*$/.test(frac);
  const body = `${currencyPrefix(currency)}${groupThousands(whole)}.${frac}`;
  if (sign === "-" && !isZero) return `−${body}`;
  if (signed && !isZero) return `+${body}`;
  return body;
}

/** Sign of a decimal string: -1, 0 or 1. */
export function decimalSign(value: string): -1 | 0 | 1 {
  const match = DECIMAL_RE.exec(value.trim());
  if (!match) return 0;
  const [, sign, whole, fraction = ""] = match;
  if (/^0*$/.test(whole) && /^0*$/.test(fraction)) return 0;
  return sign === "-" ? -1 : 1;
}

const dateTimeFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: DISPLAY_TIME_ZONE,
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});

export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : dateTimeFormatter.format(date);
}

export const TYPE_LABELS: Record<TransactionType, string> = {
  deposit: "Deposit",
  withdrawal: "Withdrawal",
  transfer: "Transfer",
};

export const STATUS_LABELS: Record<TransactionStatus, string> = {
  completed: "Completed",
  pending: "Pending",
  failed: "Failed",
};

export const ACCOUNT_TYPE_LABELS: Record<Account["type"], string> = {
  checking: "Checking",
  savings: "Savings",
};

export function labelFor<T extends string>(labels: Record<T, string>, value: string): string {
  return (labels as Record<string, string>)[value] ?? value;
}

export function accountLabel(account: Pick<AccountSummary, "name" | "masked_number">): string {
  return `${account.name} (${account.masked_number})`;
}

export function transactionDescription(tx: Transaction): string {
  if (tx.memo && tx.memo.trim()) return tx.memo;
  if (tx.type === "transfer" && tx.source_account && tx.destination_account) {
    return `Transfer from ${tx.source_account.name} to ${tx.destination_account.name}`;
  }
  return labelFor(TYPE_LABELS, tx.type);
}
