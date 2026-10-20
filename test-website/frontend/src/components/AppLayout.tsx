import { useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { ApiError } from "../api/client";
import { useSession } from "../session";
import { ApiHealth } from "./ApiHealth";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/transactions", label: "Transactions" },
  { to: "/transfer", label: "Transfer" },
  { to: "/profile", label: "Profile" },
];

export function AppLayout() {
  const { customer, logout } = useSession();
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);

  async function handleLogout() {
    setLoggingOut(true);
    setLogoutError(null);
    try {
      await logout();
    } catch (err) {
      setLogoutError(err instanceof ApiError ? err.message : "Log out failed. Please try again.");
      setLoggingOut(false);
    }
  }

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="app-header">
        <div className="app-header__inner">
          <Link to="/dashboard" className="brand" aria-label="DemoBank dashboard">
            <span className="brand__mark" aria-hidden="true">D</span>
            <span className="brand__name">DemoBank</span>
          </Link>
          <nav className="app-nav" aria-label="Main">
            {NAV_ITEMS.map((item) => (
              <NavLink key={item.to} to={item.to} className="app-nav__link">
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="app-header__session">
            {customer && (
              <span className="app-header__customer" data-testid="active-customer">
                <span className="visually-hidden">Signed in as </span>
                {customer.display_name}
              </span>
            )}
            <button
              type="button"
              className="button button--secondary button--small"
              onClick={handleLogout}
              disabled={loggingOut}
            >
              {loggingOut ? "Logging out…" : "Log out"}
            </button>
          </div>
        </div>
        {logoutError && (
          <p className="app-header__error" role="alert">
            {logoutError}
          </p>
        )}
      </header>
      <main className="page" id="main" tabIndex={-1}>
        <Outlet />
      </main>
      <footer className="app-footer">
        <span>DemoBank uses synthetic demo data only.</span>
        <ApiHealth />
      </footer>
    </div>
  );
}
