import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { ApiHealth } from "../components/ApiHealth";
import { Alert } from "../components/Status";
import { useSession, type LandingState } from "../session";

const NOTICES: Record<NonNullable<LandingState["notice"]>, { tone: "info" | "error"; text: string }> = {
  "logged-out": { tone: "info", text: "You have been logged out of the demo." },
  "session-ended": { tone: "info", text: "Your demo session has ended. Enter the demo again to continue." },
  "access-required": { tone: "info", text: "Access required: enter the demo to view that page." },
};

export function LandingPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { signIn } = useSession();
  const [entering, setEntering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const notice = (location.state as LandingState | null)?.notice;

  async function enterDemo() {
    setEntering(true);
    setError(null);
    try {
      const { customer } = await api.createDemoSession();
      signIn(customer);
      navigate("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start the demo. Please try again.");
      setEntering(false);
    }
  }

  return (
    <div className="landing">
      <main className="landing__main" id="main">
        <div className="landing__card">
          <div className="brand brand--large">
            <span className="brand__mark" aria-hidden="true">D</span>
            <span className="brand__name">DemoBank</span>
          </div>
          <h1 className="landing__title">Your accounts, in one simple dashboard.</h1>
          <p className="landing__lead">
            Explore a fictional checking and savings account, review transaction history, and move money between
            your own accounts. No password needed, and every record is synthetic.
          </p>
          {notice && !error && <Alert tone={NOTICES[notice].tone}>{NOTICES[notice].text}</Alert>}
          {error && (
            <Alert tone="error" title="The demo did not start.">
              {error}
            </Alert>
          )}
          <button
            type="button"
            className="button button--primary button--large"
            onClick={enterDemo}
            disabled={entering}
            aria-busy={entering}
          >
            {entering ? "Entering demo…" : "Enter Demo"}
          </button>
          <p className="landing__fineprint">
            DemoBank never connects to a real bank or moves real money.
          </p>
        </div>
      </main>
      <footer className="app-footer app-footer--landing">
        <ApiHealth />
      </footer>
    </div>
  );
}
