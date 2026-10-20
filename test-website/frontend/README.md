# Frontend Source Workspace

React + TypeScript + Vite browser UI for DemoBank. It consumes the backend API and does not connect directly to PostgreSQL. All balances, transfer outcomes, and transaction history come from the API. See the repository PRD for expected behavior.

## Run locally

Requires Node.js 20+ and the backend running on `http://localhost:8000` (start order: database, migrations/seeds, backend, frontend).

```bash
npm install
npm run dev        # http://localhost:5173
```

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:8000` | Base URL of the DemoBank API. Copy `.env.example` to `.env` to override. |

Other scripts: `npm run build` (type-check and production build to `dist/`), `npm run preview` (serve the build on port 5173), `npm run typecheck`.

Every request uses `credentials: "include"` so the browser sends the demo session cookie; the backend must allow credentialed CORS from the frontend origin.

## Routes

| Path | View | API calls |
|---|---|---|
| `/` | Landing with **Enter Demo** | `POST /api/demo/session`, `GET /health` |
| `/dashboard` | Checking and savings balances | `GET /api/me`, `GET /api/accounts` |
| `/accounts/:accountId` | Account balance and history | `GET /api/accounts/{id}`, `GET /api/transactions?account_id=` |
| `/transactions` | Combined history with account, type, status, start/end date filters (kept in the URL) | `GET /api/accounts`, `GET /api/transactions?…` |
| `/transfer` | Transfer form and returned balances | `GET /api/accounts`, `POST /api/transfers` |
| `/profile` | Display name and masked email | `GET /api/me` |

Protected routes confirm the session with `GET /api/me` on direct entry or refresh; a `401` from any protected call returns to the landing page. **Log out** calls `DELETE /api/demo/session`.

## Source layout

- `src/api/` – typed API client (`client.ts`) shared by all views, and contract response types (`types.ts`).
- `src/session.tsx`, `src/components/RequireSession.tsx` – session state and route gating.
- `src/components/` – layout/header, account card, transaction table, loading/empty/error states, service health indicator.
- `src/pages/` – one component per route.
- `src/lib/` – decimal-string money formatting (no floating point), Pacific-time date display, data-loading hook.

Transaction times are shown in `America/Los_Angeles`, the timezone the API uses for date filters, so the dates shown line up with filter boundaries.
