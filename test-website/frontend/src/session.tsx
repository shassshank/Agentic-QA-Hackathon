import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, onUnauthorized } from "./api/client";
import type { Customer } from "./api/types";

/** Reasons the landing page may be showing after leaving a protected view. */
export type LandingNotice = "logged-out" | "session-ended" | "access-required";

export interface LandingState {
  notice?: LandingNotice;
}

interface SessionContextValue {
  /** The customer from the last successful session call; null when not known to be signed in. */
  customer: Customer | null;
  /** Set when this tab ended the session (logout or a 401); protected routes redirect without re-checking. */
  endedReason: LandingNotice | null;
  signIn: (customer: Customer) => void;
  logout: () => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [endedReason, setEndedReason] = useState<LandingNotice | null>(null);

  const endSession = useCallback((reason: LandingNotice) => {
    setCustomer(null);
    setEndedReason(reason);
  }, []);

  // Any 401 from a protected endpoint means the session cookie is gone or expired.
  useEffect(() => onUnauthorized(() => endSession("session-ended")), [endSession]);

  const signIn = useCallback((next: Customer) => {
    setCustomer(next);
    setEndedReason(null);
  }, []);

  const logout = useCallback(async () => {
    await api.endDemoSession();
    endSession("logged-out");
  }, [endSession]);

  const value = useMemo(
    () => ({ customer, endedReason, signIn, logout }),
    [customer, endedReason, signIn, logout],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside SessionProvider");
  return ctx;
}
