# Running DemoBank locally

The application has three parts that start in this order: the PostgreSQL database, the
FastAPI backend, and the React frontend. The browser talks only to the backend; the backend
is the only part that connects to PostgreSQL.

| Part | Directory | Address |
|---|---|---|
| PostgreSQL 16 (`demobank`) | `database/` | `localhost:5432` |
| FastAPI backend | `backend/` | `http://localhost:8000` (`/health`, `/docs`) |
| React + Vite frontend | `frontend/` | `http://localhost:5173` |

Requirements: Docker with Compose, Python 3.11+, and Node.js 20+.

## 1. Database, migrations, and seed

```bash
cd database
make up       # starts PostgreSQL; a new volume runs the migration and seed automatically
make reset    # reapplies the migration and restores the seed
```

Run `make reset` whenever you want to return to the seeded demo state. See
[`database/README.md`](database/README.md) for the other targets.

## 2. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Edit `.env` so the backend can reach the local database started in step 1:

```text
DATABASE_URL=postgresql://demobank:demobank_local@localhost:5432/demobank
SESSION_SECRET=<any long random local value>
```

These are local-only demo credentials from `database/compose.yaml`. Then start the API:

```bash
set -a; source .env; set +a
uvicorn demobank.main:app --host 0.0.0.0 --port 8000 --reload
```

The backend allows credentialed CORS requests from `http://localhost:5173`.

## 3. Frontend

```bash
cd frontend
npm ci
npm run dev
```

`VITE_API_BASE_URL` defaults to `http://localhost:8000`; copy `.env.example` to `.env` only to
override it. Open <http://localhost:5173> and choose **Enter Demo**. Use `localhost` rather
than `127.0.0.1` so the browser origin matches the backend's CORS setting.

## Build check

```bash
(cd frontend && npm run typecheck && npm run build)
```
