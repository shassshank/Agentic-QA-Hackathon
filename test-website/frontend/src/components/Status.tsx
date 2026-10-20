import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { ApiError } from "../api/client";

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="state state--loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({
  title = "Something went wrong",
  error,
  onRetry,
  backTo = { to: "/dashboard", label: "Back to dashboard" },
}: {
  title?: string;
  error: ApiError;
  onRetry?: () => void;
  backTo?: { to: string; label: string } | null;
}) {
  return (
    <div className="state state--error" role="alert">
      <h2 className="state__title">{title}</h2>
      <p>{error.message}</p>
      <div className="button-row">
        {onRetry && (
          <button type="button" className="button button--secondary" onClick={onRetry}>
            Try again
          </button>
        )}
        {backTo && (
          <Link className="button button--ghost" to={backTo.to}>
            {backTo.label}
          </Link>
        )}
      </div>
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state state--empty" role="status">
      <h3 className="state__title">{title}</h3>
      {children}
    </div>
  );
}

export function Alert({
  tone,
  title,
  children,
}: {
  tone: "error" | "success" | "info";
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div className={`alert alert--${tone}`} role={tone === "error" ? "alert" : "status"}>
      {title && <strong className="alert__title">{title}</strong>}
      {children && <div>{children}</div>}
    </div>
  );
}
