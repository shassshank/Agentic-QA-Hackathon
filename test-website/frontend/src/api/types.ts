// Response shapes from the DemoBank HTTP contract. Field names must match the backend exactly.

export interface Customer {
  id: string;
  display_name: string;
  masked_email: string;
}

export type AccountType = "checking" | "savings";

export interface Account {
  id: string;
  name: string;
  type: AccountType;
  masked_number: string;
  currency: string;
  /** Decimal string with two digits, e.g. "1250.00". */
  balance: string;
}

export type TransactionType = "deposit" | "withdrawal" | "transfer";
export type TransactionStatus = "completed" | "pending" | "failed";

export interface AccountSummary {
  id: string;
  name: string;
  masked_number: string;
}

export interface AccountDelta {
  account_id: string;
  /** Signed decimal string, e.g. "-25.00". */
  amount_delta: string;
}

export interface Transaction {
  id: string;
  type: TransactionType;
  status: TransactionStatus;
  amount: string;
  memo: string | null;
  /** ISO-8601 UTC timestamp. */
  occurred_at: string;
  source_account: AccountSummary | null;
  destination_account: AccountSummary | null;
  account_deltas: AccountDelta[];
}

export interface TransactionFilters {
  account_id?: string;
  type?: TransactionType | "";
  status?: TransactionStatus | "";
  /** YYYY-MM-DD, inclusive. */
  start_date?: string;
  /** YYYY-MM-DD, inclusive. */
  end_date?: string;
}

export interface TransferRequest {
  source_account_id: string;
  destination_account_id: string;
  amount: string;
  memo: string;
}

export interface TransferResult {
  transaction: Transaction;
  accounts: Account[];
}

export const TRANSACTION_TYPES: TransactionType[] = ["deposit", "withdrawal", "transfer"];
export const TRANSACTION_STATUSES: TransactionStatus[] = ["completed", "pending", "failed"];
