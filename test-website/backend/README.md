# DemoBank backend

FastAPI service for the shared DemoBank application. It maps the PostgreSQL tables owned by
`../database` and exposes the frozen API consumed by `../frontend`.

## Run

Python 3.11+ is required. Start the database and apply its migrations/seeds first, then:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# Edit .env with the local database credentials and a random SESSION_SECRET.
set -a; source .env; set +a
uvicorn demobank.main:app --host 0.0.0.0 --port 8000 --reload
```

- Health: <http://localhost:8000/health>
- Interactive OpenAPI: <http://localhost:8000/docs>
- OpenAPI JSON: <http://localhost:8000/openapi.json>

The frontend origin `http://localhost:5173` is allowed with credentialed CORS. Session tokens
are random opaque cookie values; only keyed hashes are stored in `demo_sessions`.
