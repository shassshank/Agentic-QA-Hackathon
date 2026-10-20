import { useEffect, useState } from "react";
import { Navigate, Outlet } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useSession, type LandingState } from "../session";
import { ErrorState, LoadingState } from "./Status";

/**
 * Gates protected routes. When the app does not already hold a customer (direct entry or refresh),
 * it confirms the session cookie with GET /api/me before rendering anything protected.
 */
export function RequireSession() {
  const { customer, endedReason, signIn } = useSession();
  const [check, setCheck] = useState<"checking" | "denied" | ApiError | null>(customer ? null : "checking");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (customer || endedReason) {
      setCheck(null);
      return;
    }
    const controller = new AbortController();
    setCheck("checking");
    api.getMe(controller.signal, false).then(
      (me) => {
        if (controller.signal.aborted) return;
        signIn(me);
        setCheck(null);
      },
      (err: unknown) => {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError && err.isUnauthenticated) setCheck("denied");
        else setCheck(err instanceof ApiError ? err : new ApiError(0, "unexpected", "Could not verify your session."));
      },
    );
    return () => controller.abort();
  }, [customer, endedReason, signIn, attempt]);

  if (!customer && endedReason) {
    return <Navigate to="/" replace state={{ notice: endedReason } satisfies LandingState} />;
  }
  if (check === "denied") {
    return <Navigate to="/" replace state={{ notice: "access-required" } satisfies LandingState} />;
  }
  if (check === "checking" || (!customer && check === null)) {
    return (
      <main className="page page--centered" id="main">
        <LoadingState label="Checking your demo session…" />
      </main>
    );
  }
  if (check instanceof ApiError) {
    return (
      <main className="page page--centered" id="main">
        <ErrorState
          title="We couldn't verify your session"
          error={check}
          onRetry={() => setAttempt((n) => n + 1)}
          backTo={{ to: "/", label: "Back to start" }}
        />
      </main>
    );
  }
  return <Outlet />;
}
