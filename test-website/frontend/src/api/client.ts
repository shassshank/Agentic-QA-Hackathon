import type {
  Account,
  Customer,
  Transaction,
  TransactionFilters,
  TransferRequest,
  TransferResult,
} from "./types";

export const API_BASE_URL: string = (
  (import.meta.env.VITE_API_BASE_URL as string | undefined) || "http://localhost:8000"
).replace(/\/+$/, "");

/** Error raised for any non-2xx response or network failure. `status` is 0 for network errors. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  get isUnauthenticated(): boolean {
    return this.status === 401;
  }
}

type UnauthorizedListener = () => void;
const unauthorizedListeners = new Set<UnauthorizedListener>();

/** Subscribe to 401 responses from protected endpoints so the app can end the session. */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

function fallbackMessage(status: number): string {
  switch (status) {
    case 401:
      return "Your demo session has ended. Enter the demo again to continue.";
    case 404:
      return "The requested item could not be found.";
    case 409:
      return "The request conflicts with the current state of your accounts.";
    case 422:
      return "Some of the submitted information is invalid.";
    default:
      return status >= 500
        ? "DemoBank ran into a problem on its side. Please try again."
        : "The request could not be completed.";
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let code = `http_${response.status}`;
  let message = fallbackMessage(response.status);
  try {
    const body: unknown = await response.json();
    const err = (body as { error?: { code?: unknown; message?: unknown } } | null)?.error;
    if (err && typeof err === "object") {
      if (typeof err.code === "string" && err.code) code = err.code;
      // Only surface the backend's human-readable message; never raw server details for 5xx.
      if (typeof err.message === "string" && err.message && response.status < 500) {
        message = err.message;
      }
    }
  } catch {
    // Non-JSON error body: keep the generic message.
  }
  return new ApiError(response.status, code, message);
}

interface RequestOptions {
  method?: "GET" | "POST" | "DELETE";
  body?: unknown;
  query?: Record<string, string | undefined>;
  signal?: AbortSignal;
  /** Protected requests broadcast 401s; session-bootstrap requests handle them locally. */
  notifyUnauthorized?: boolean;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, query, signal, notifyUnauthorized = true } = options;
  const url = new URL(API_BASE_URL + path);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== "") url.searchParams.set(key, value);
    }
  }

  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(url.toString(), {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      credentials: "include",
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(
      0,
      "network_error",
      "Could not reach the DemoBank service. Check your connection and try again.",
    );
  }

  if (!response.ok) {
    const error = await parseError(response);
    if (error.isUnauthenticated && notifyUnauthorized) {
      unauthorizedListeners.forEach((listener) => listener());
    }
    throw error;
  }

  if (response.status === 204) return undefined as T;
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(response.status, "invalid_response", "DemoBank returned an unexpected response.");
  }
}

export const api = {
  createDemoSession(): Promise<{ customer: Customer }> {
    return request("/api/demo/session", { method: "POST", notifyUnauthorized: false });
  },

  endDemoSession(): Promise<void> {
    return request("/api/demo/session", { method: "DELETE", notifyUnauthorized: false });
  },

  getMe(signal?: AbortSignal, notifyUnauthorized = true): Promise<Customer> {
    return request("/api/me", { signal, notifyUnauthorized });
  },

  async listAccounts(signal?: AbortSignal): Promise<Account[]> {
    const data = await request<{ accounts: Account[] }>("/api/accounts", { signal });
    return data.accounts;
  },

  async getAccount(accountId: string, signal?: AbortSignal): Promise<Account> {
    const data = await request<{ account: Account }>(
      `/api/accounts/${encodeURIComponent(accountId)}`,
      { signal },
    );
    return data.account;
  },

  async listTransactions(filters: TransactionFilters = {}, signal?: AbortSignal): Promise<Transaction[]> {
    const data = await request<{ transactions: Transaction[] }>("/api/transactions", {
      signal,
      query: {
        account_id: filters.account_id,
        type: filters.type,
        status: filters.status,
        start_date: filters.start_date,
        end_date: filters.end_date,
      },
    });
    return data.transactions;
  },

  createTransfer(payload: TransferRequest): Promise<TransferResult> {
    return request("/api/transfers", { method: "POST", body: payload });
  },

  health(signal?: AbortSignal): Promise<{ status: string }> {
    return request("/health", { signal, notifyUnauthorized: false });
  },
};
