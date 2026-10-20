import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="state state--empty">
      <h1 className="state__title">Page not found</h1>
      <p>We couldn't find that page.</p>
      <Link className="button button--secondary" to="/dashboard">
        Back to dashboard
      </Link>
    </div>
  );
}
