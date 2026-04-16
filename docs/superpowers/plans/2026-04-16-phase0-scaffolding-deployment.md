# Phase 0: Project Scaffolding + Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the project foundation with a working dev environment, FastAPI backend, React frontend, database setup, and Nix packaging so that every subsequent phase can be built, run, and tested immediately.

**Architecture:** Monolith FastAPI application serving a React/TypeScript SPA as static assets. SQLAlchemy ORM with Alembic migrations, database-agnostic (SQLite default, PostgreSQL supported). All packaging via Nix — no Dockerfile. Development uses nix-shell with a Python virtualenv and Node.js for the frontend build.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, SQLAlchemy, Alembic, Pydantic, React 18, TypeScript, Vite, Nix

---

## File Structure

```
SkyNetControl/
├── backend/
│   ├── __init__.py
│   ├── app.py                # FastAPI application factory
│   ├── config.py             # Pydantic settings (database URL, etc.)
│   └── db/
│       ├── __init__.py
│       ├── base.py           # Declarative base for models
│       └── session.py        # Engine creation, session factory
├── alembic/
│   ├── env.py                # Alembic environment (uses backend.db)
│   ├── script.py.mako        # Migration template
│   └── versions/             # Migration scripts
├── alembic.ini               # Alembic config
├── frontend/
│   ├── index.html            # Vite entry HTML
│   ├── package.json          # Node dependencies
│   ├── tsconfig.json         # TypeScript config
│   ├── vite.config.ts        # Vite config with API proxy
│   └── src/
│       ├── main.tsx          # React entry point
│       ├── App.tsx           # Root component
│       └── App.css           # Base styles
├── tests/
│   ├── __init__.py
│   ├── conftest.py           # Shared fixtures (test client, test DB)
│   └── test_health.py        # Health endpoint tests
├── pyproject.toml            # Python project config + dependencies
├── shell.nix                 # Development environment
├── default.nix               # Nix package (backend + frontend)
├── frontend.nix              # Frontend build derivation
├── module.nix                # NixOS service module
├── oci.nix                   # OCI container image
└── .gitignore
```

---

### Task 1: Project Structure + .gitignore

**Files:**
- Create: `.gitignore`
- Create: `backend/__init__.py`
- Create: `backend/db/__init__.py`
- Create: `tests/__init__.py`
- Create: `alembic/versions/.gitkeep`

- [ ] **Step 1: Create .gitignore**

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.venv/
*.egg

# Node
node_modules/
frontend/dist/

# Database
*.db
*.sqlite3

# IDE
.idea/
.vscode/
*.swp
*.swo

# Nix
result
result-*

# Alembic
alembic/versions/__pycache__/

# Superpowers
.superpowers/

# Environment
.env
.env.local
```

- [ ] **Step 2: Create empty Python package files**

Create these empty files to establish the Python package structure:

`backend/__init__.py`:
```python
```

`backend/db/__init__.py`:
```python
```

`tests/__init__.py`:
```python
```

`alembic/versions/.gitkeep`:
```
```

- [ ] **Step 3: Commit**

```bash
git add .gitignore backend/__init__.py backend/db/__init__.py tests/__init__.py alembic/versions/.gitkeep
git commit -m "chore: initial project structure and gitignore"
```

---

### Task 2: Development Shell (shell.nix)

**Files:**
- Create: `shell.nix`

- [ ] **Step 1: Create shell.nix**

```nix
{ pkgs ? import <nixpkgs> {} }:

let
  python = pkgs.python312;
in
pkgs.mkShell {
  buildInputs = [
    python
    python.pkgs.pip
    python.pkgs.virtualenv
    pkgs.nodejs_22
    pkgs.nodePackages.npm
  ];

  shellHook = ''
    # Set up Python virtualenv
    if [ ! -d .venv ]; then
      echo "Creating Python virtualenv..."
      ${python}/bin/python -m venv .venv
    fi
    source .venv/bin/activate

    # Install Python deps if pyproject.toml exists
    if [ -f pyproject.toml ]; then
      pip install -e ".[dev]" --quiet 2>/dev/null || true
    fi

    # Install frontend deps if package.json exists
    if [ -f frontend/package.json ] && [ ! -d frontend/node_modules ]; then
      echo "Installing frontend dependencies..."
      (cd frontend && npm install)
    fi

    echo "SkyNetControl dev environment ready."
  '';
}
```

- [ ] **Step 2: Verify the shell loads**

Run: `nix-shell --run "python --version && node --version"`

Expected output (versions may vary):
```
Python 3.12.x
v22.x.x
```

- [ ] **Step 3: Commit**

```bash
git add shell.nix
git commit -m "chore: add nix-shell development environment"
```

---

### Task 3: Python Project Configuration

**Files:**
- Create: `pyproject.toml`
- Create: `backend/config.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68.0", "setuptools-scm>=8.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "skynetcontrol"
version = "0.1.0"
description = "Winlink net management application"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.34.0",
    "sqlalchemy>=2.0.0",
    "alembic>=1.14.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "httpx>=0.28.0",
    "pytest-asyncio>=0.24.0",
]

[tool.setuptools.packages.find]
include = ["backend*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create backend/config.py**

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///skynetcontrol.db"
    debug: bool = False

    model_config = {"env_prefix": "SKYNET_"}


settings = Settings()
```

- [ ] **Step 3: Install dependencies in nix-shell**

Run: `nix-shell --run "pip install -e '.[dev]' --quiet && python -c 'import fastapi; print(fastapi.__version__)'"`

Expected: FastAPI version number printed (e.g., `0.115.x`)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml backend/config.py
git commit -m "chore: add Python project config and settings"
```

---

### Task 4: FastAPI Health Endpoint (TDD)

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_health.py`
- Create: `backend/app.py`

- [ ] **Step 1: Create test fixtures**

`tests/conftest.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```

- [ ] **Step 2: Write the failing test**

`tests/test_health.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_health_includes_version(client):
    response = await client.get("/api/health")
    data = response.json()
    assert "version" in data
    assert data["version"] == "0.1.0"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_health.py -v"`

Expected: FAIL — `ImportError: cannot import name 'create_app' from 'backend.app'`

- [ ] **Step 4: Implement the FastAPI app**

`backend/app.py`:

```python
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="SkyNetControl", version="0.1.0")

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    return app
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_health.py -v"`

Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_health.py backend/app.py
git commit -m "feat: add FastAPI app with health endpoint"
```

---

### Task 5: Database Setup (SQLAlchemy + Alembic)

**Files:**
- Create: `backend/db/base.py`
- Create: `backend/db/session.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `tests/test_database.py`

- [ ] **Step 1: Write the failing database test**

`tests/test_database.py`:

```python
import pytest
from sqlalchemy import text

from backend.db.session import create_engine_from_url, create_session_factory


@pytest.mark.asyncio
async def test_sqlite_engine_connects():
    engine = create_engine_from_url("sqlite:///")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        assert result.scalar() == 1
    engine.dispose()


@pytest.mark.asyncio
async def test_session_factory_creates_session():
    engine = create_engine_from_url("sqlite:///")
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        result = session.execute(text("SELECT 1"))
        assert result.scalar() == 1
    engine.dispose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `nix-shell --run "pytest tests/test_database.py -v"`

Expected: FAIL — `ImportError: cannot import name 'create_engine_from_url'`

- [ ] **Step 3: Implement database session module**

`backend/db/base.py`:

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

`backend/db/session.py`:

```python
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def create_engine_from_url(url: str) -> Engine:
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(url, connect_args=connect_args)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `nix-shell --run "pytest tests/test_database.py -v"`

Expected: 2 passed

- [ ] **Step 5: Create Alembic configuration**

`alembic.ini`:

```ini
[alembic]
script_location = alembic
sqlalchemy.url = sqlite:///skynetcontrol.db

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

`alembic/env.py`:

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`alembic/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 6: Verify Alembic works**

Run: `nix-shell --run "alembic check"`

Expected: No error (may say "No new upgrade operations detected")

- [ ] **Step 7: Commit**

```bash
git add backend/db/base.py backend/db/session.py alembic.ini alembic/env.py alembic/script.py.mako tests/test_database.py
git commit -m "feat: add SQLAlchemy database setup and Alembic migrations"
```

---

### Task 6: Wire Database Into FastAPI App

**Files:**
- Modify: `backend/app.py`
- Modify: `backend/config.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_app_db.py`

- [ ] **Step 1: Write the failing test**

`tests/test_app_db.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_health_includes_database_status(client):
    response = await client.get("/api/health")
    data = response.json()
    assert data["database"] == "connected"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix-shell --run "pytest tests/test_app_db.py -v"`

Expected: FAIL — `assert None == 'connected'` or KeyError

- [ ] **Step 3: Update conftest to use test database**

`tests/conftest.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import create_app
from backend.config import Settings


@pytest.fixture
def test_settings():
    return Settings(database_url="sqlite:///", debug=True)


@pytest.fixture
def app(test_settings):
    return create_app(settings=test_settings)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```

- [ ] **Step 4: Update app to accept settings and check database**

`backend/app.py`:

```python
from fastapi import FastAPI
from sqlalchemy import text

from backend.config import Settings, settings as default_settings
from backend.db.session import create_engine_from_url, create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or default_settings
    app = FastAPI(title="SkyNetControl", version="0.1.0")

    engine = create_engine_from_url(settings.database_url)
    session_factory = create_session_factory(engine)
    app.state.engine = engine
    app.state.session_factory = session_factory

    @app.get("/api/health")
    async def health():
        db_status = "disconnected"
        try:
            with session_factory() as session:
                session.execute(text("SELECT 1"))
                db_status = "connected"
        except Exception:
            pass
        return {"status": "ok", "version": "0.1.0", "database": db_status}

    return app
```

- [ ] **Step 5: Run all tests to verify they pass**

Run: `nix-shell --run "pytest tests/ -v"`

Expected: All tests pass (test_health.py and test_app_db.py and test_database.py)

- [ ] **Step 6: Commit**

```bash
git add backend/app.py backend/config.py tests/conftest.py tests/test_app_db.py
git commit -m "feat: wire database into FastAPI app with health check"
```

---

### Task 7: React/TypeScript Frontend Skeleton

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/App.css`

- [ ] **Step 1: Create package.json**

```json
{
  "name": "skynetcontrol-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "^5.6.0",
    "vite": "^6.0.0"
  }
}
```

- [ ] **Step 2: Create TypeScript config**

`frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedIndexedAccess": true
  },
  "include": ["src"]
}
```

- [ ] **Step 3: Create Vite config with API proxy**

`frontend/vite.config.ts`:

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
```

- [ ] **Step 4: Create entry HTML and React components**

`frontend/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>SkyNetControl</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`frontend/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./App.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
```

`frontend/src/App.tsx`:

```tsx
import { useEffect, useState } from "react";

interface HealthStatus {
  status: string;
  version: string;
  database: string;
}

function App() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((res) => res.json())
      .then(setHealth)
      .catch((err) => setError(err.message));
  }, []);

  return (
    <div className="app">
      <h1>SkyNetControl</h1>
      <p>Winlink Net Management</p>
      {error && <p className="error">API Error: {error}</p>}
      {health && (
        <div className="status">
          <p>Status: {health.status}</p>
          <p>Version: {health.version}</p>
          <p>Database: {health.database}</p>
        </div>
      )}
    </div>
  );
}

export default App;
```

`frontend/src/App.css`:

```css
.app {
  max-width: 800px;
  margin: 2rem auto;
  padding: 0 1rem;
  font-family: system-ui, -apple-system, sans-serif;
}

.status {
  background: #f0f0f0;
  padding: 1rem;
  border-radius: 4px;
  margin-top: 1rem;
}

.error {
  color: #c00;
}
```

- [ ] **Step 5: Install frontend dependencies and verify build**

Run: `nix-shell --run "cd frontend && npm install && npm run build"`

Expected: Build completes successfully, `frontend/dist/` directory created with `index.html` and JS assets.

- [ ] **Step 6: Commit**

```bash
git add frontend/
git commit -m "feat: add React/TypeScript frontend skeleton with Vite"
```

---

### Task 8: Serve Frontend From FastAPI

**Files:**
- Modify: `backend/app.py`
- Modify: `pyproject.toml`
- Create: `tests/test_frontend_serving.py`

- [ ] **Step 1: Write the failing test**

`tests/test_frontend_serving.py`:

```python
import os
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import create_app
from backend.config import Settings


@pytest.mark.asyncio
async def test_serves_index_html_at_root():
    with tempfile.TemporaryDirectory() as static_dir:
        index_path = os.path.join(static_dir, "index.html")
        with open(index_path, "w") as f:
            f.write("<html><body>SkyNetControl</body></html>")

        settings = Settings(database_url="sqlite:///", static_dir=static_dir)
        app = create_app(settings=settings)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/")
            assert response.status_code == 200
            assert "SkyNetControl" in response.text


@pytest.mark.asyncio
async def test_api_routes_take_priority_over_static():
    with tempfile.TemporaryDirectory() as static_dir:
        index_path = os.path.join(static_dir, "index.html")
        with open(index_path, "w") as f:
            f.write("<html><body>SkyNetControl</body></html>")

        settings = Settings(database_url="sqlite:///", static_dir=static_dir)
        app = create_app(settings=settings)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `nix-shell --run "pytest tests/test_frontend_serving.py -v"`

Expected: FAIL — `Settings` doesn't accept `static_dir`

- [ ] **Step 3: Add static_dir to config**

`backend/config.py`:

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///skynetcontrol.db"
    static_dir: str = "frontend/dist"
    debug: bool = False

    model_config = {"env_prefix": "SKYNET_"}
```

- [ ] **Step 4: Update app to serve static files**

`backend/app.py`:

```python
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from backend.config import Settings, settings as default_settings
from backend.db.session import create_engine_from_url, create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or default_settings
    app = FastAPI(title="SkyNetControl", version="0.1.0")

    engine = create_engine_from_url(settings.database_url)
    session_factory = create_session_factory(engine)
    app.state.engine = engine
    app.state.session_factory = session_factory

    @app.get("/api/health")
    async def health():
        db_status = "disconnected"
        try:
            with session_factory() as session:
                session.execute(text("SELECT 1"))
                db_status = "connected"
        except Exception:
            pass
        return {"status": "ok", "version": "0.1.0", "database": db_status}

    # Serve frontend static files if the directory exists
    if os.path.isdir(settings.static_dir):
        app.mount("/assets", StaticFiles(directory=os.path.join(settings.static_dir, "assets")), name="assets")

        @app.get("/{path:path}")
        async def serve_frontend(path: str):
            # Serve the file if it exists, otherwise fall back to index.html (SPA routing)
            file_path = os.path.join(settings.static_dir, path)
            if path and os.path.isfile(file_path):
                return FileResponse(file_path)
            return FileResponse(os.path.join(settings.static_dir, "index.html"))

    return app
```

- [ ] **Step 5: Run all tests to verify they pass**

Run: `nix-shell --run "pytest tests/ -v"`

Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add backend/app.py backend/config.py tests/test_frontend_serving.py
git commit -m "feat: serve React frontend static files from FastAPI"
```

---

### Task 9: Nix Package (default.nix + frontend.nix)

**Files:**
- Create: `frontend.nix`
- Create: `default.nix`

- [ ] **Step 1: Create frontend.nix**

This builds the React frontend as a Nix derivation.

```nix
{ pkgs ? import <nixpkgs> {} }:

pkgs.buildNpmPackage {
  pname = "skynetcontrol-frontend";
  version = "0.1.0";
  src = ./frontend;

  npmDepsHash = "";  # Run nix-build frontend.nix once to get the correct hash from the error message, then update this value

  buildPhase = ''
    npm run build
  '';

  installPhase = ''
    cp -r dist $out
  '';
}
```

Note: The `npmDepsHash` must be populated after the first build attempt. Run `nix-build frontend.nix` — it will fail with the correct hash. Update the value and rebuild.

- [ ] **Step 2: Build frontend and capture the correct npmDepsHash**

Run: `nix-build frontend.nix 2>&1 | grep "got:"`

Copy the hash from the output and update `npmDepsHash` in `frontend.nix`.

- [ ] **Step 3: Verify frontend.nix builds**

Run: `nix-build frontend.nix`

Expected: Builds successfully, `result/` contains `index.html` and `assets/` directory.

Run: `ls result/index.html result/assets/`

Expected: Files listed.

- [ ] **Step 4: Create default.nix**

```nix
{ pkgs ? import <nixpkgs> {} }:

let
  python = pkgs.python312;
  frontend = import ./frontend.nix { inherit pkgs; };
in
python.pkgs.buildPythonApplication {
  pname = "skynetcontrol";
  version = "0.1.0";
  src = ./.;
  pyproject = true;

  build-system = [ python.pkgs.setuptools ];

  dependencies = with python.pkgs; [
    fastapi
    uvicorn
    sqlalchemy
    alembic
    pydantic
    pydantic-settings
  ];

  postInstall = ''
    mkdir -p $out/share/skynetcontrol
    cp -r ${frontend} $out/share/skynetcontrol/static
    cp alembic.ini $out/share/skynetcontrol/
    cp -r alembic $out/share/skynetcontrol/alembic
  '';

  postFixup = ''
    wrapProgram $out/bin/uvicorn \
      --set SKYNET_STATIC_DIR "$out/share/skynetcontrol/static"
  '';

  meta = {
    description = "Winlink net management application";
    mainProgram = "uvicorn";
  };
}
```

- [ ] **Step 5: Verify default.nix builds**

Run: `nix-build default.nix`

Expected: Builds successfully.

Run: `ls result/share/skynetcontrol/static/index.html`

Expected: File exists.

- [ ] **Step 6: Verify the built package runs**

Run: `result/bin/uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000 &`

Then: `curl http://localhost:8000/api/health`

Expected: `{"status":"ok","version":"0.1.0","database":"connected"}`

Stop the server: `kill %1`

- [ ] **Step 7: Commit**

```bash
git add frontend.nix default.nix
git commit -m "feat: add Nix package for backend and frontend"
```

---

### Task 10: NixOS Module (module.nix)

**Files:**
- Create: `module.nix`

- [ ] **Step 1: Create module.nix**

```nix
{ config, lib, pkgs, ... }:

let
  cfg = config.services.skynetcontrol;
  skynetcontrol = import ./default.nix { inherit pkgs; };
in
{
  options.services.skynetcontrol = {
    enable = lib.mkEnableOption "SkyNetControl Winlink net management";

    port = lib.mkOption {
      type = lib.types.port;
      default = 8000;
      description = "Port to listen on.";
    };

    host = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "Address to bind to.";
    };

    stateDir = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/skynetcontrol";
      description = "Directory for database and runtime state.";
    };

    databaseUrl = lib.mkOption {
      type = lib.types.str;
      default = "sqlite:////var/lib/skynetcontrol/skynetcontrol.db";
      description = "SQLAlchemy database URL. Defaults to SQLite in stateDir.";
    };

    settings = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = {};
      description = "Additional environment variables (SKYNET_ prefix added automatically if missing).";
      example = {
        DEBUG = "true";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.skynetcontrol = {
      description = "SkyNetControl Winlink Net Management";
      after = [ "network.target" ];
      wantedBy = [ "multi-user.target" ];

      environment = {
        SKYNET_DATABASE_URL = cfg.databaseUrl;
      } // lib.mapAttrs' (name: value:
        let
          envName = if lib.hasPrefix "SKYNET_" name then name else "SKYNET_${name}";
        in
        lib.nameValuePair envName value
      ) cfg.settings;

      serviceConfig = {
        Type = "simple";
        ExecStartPre = "${skynetcontrol}/bin/alembic -c ${skynetcontrol}/share/skynetcontrol/alembic.ini upgrade head";
        ExecStart = "${skynetcontrol}/bin/uvicorn backend.app:create_app --factory --host ${cfg.host} --port ${toString cfg.port}";
        StateDirectory = "skynetcontrol";
        DynamicUser = true;
        Restart = "on-failure";
        RestartSec = 5;

        # Hardening
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        ReadWritePaths = [ cfg.stateDir ];
      };
    };
  };
}
```

- [ ] **Step 2: Validate the module syntax**

Run: `nix-instantiate --eval -E 'let pkgs = import <nixpkgs> {}; in (import ./module.nix { config = { services.skynetcontrol = { enable = false; }; }; lib = pkgs.lib; pkgs = pkgs; }).options.services.skynetcontrol.enable.description or "ok"'`

Expected: No syntax errors. (The exact output may vary — we're checking that the Nix expression parses.)

- [ ] **Step 3: Commit**

```bash
git add module.nix
git commit -m "feat: add NixOS service module"
```

---

### Task 11: OCI Image (oci.nix)

**Files:**
- Create: `oci.nix`

- [ ] **Step 1: Create oci.nix**

```nix
{ pkgs ? import <nixpkgs> {} }:

let
  skynetcontrol = import ./default.nix { inherit pkgs; };
  python = pkgs.python312;
in
pkgs.dockerTools.buildLayeredImage {
  name = "skynetcontrol";
  tag = "latest";

  contents = [
    skynetcontrol
    pkgs.coreutils
    pkgs.bashInteractive
  ];

  config = {
    Cmd = [
      "${skynetcontrol}/bin/uvicorn"
      "backend.app:create_app"
      "--factory"
      "--host" "0.0.0.0"
      "--port" "8000"
    ];
    Env = [
      "SKYNET_DATABASE_URL=sqlite:////data/skynetcontrol.db"
      "SKYNET_STATIC_DIR=${skynetcontrol}/share/skynetcontrol/static"
    ];
    ExposedPorts = {
      "8000/tcp" = {};
    };
    Volumes = {
      "/data" = {};
    };
    WorkingDir = "/";
  };
}
```

- [ ] **Step 2: Build the OCI image**

Run: `nix-build oci.nix`

Expected: Builds successfully, `result` is a symlink to a `.tar.gz` image file.

- [ ] **Step 3: Verify the image loads (if Docker/Podman available)**

If Docker or Podman is available, verify:

Run: `docker load < result && docker run --rm -p 8000:8000 skynetcontrol:latest &`

Then: `curl http://localhost:8000/api/health`

Expected: `{"status":"ok","version":"0.1.0","database":"connected"}`

Stop: `docker stop $(docker ps -q --filter ancestor=skynetcontrol:latest)`

If Docker/Podman is not available, skip this step — the image built successfully.

- [ ] **Step 4: Commit**

```bash
git add oci.nix
git commit -m "feat: add OCI container image build"
```

---

### Task 12: Add Run Script for Development

**Files:**
- Create: `run-dev.sh`

- [ ] **Step 1: Create development run script**

`run-dev.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "Starting SkyNetControl development servers..."

# Run Alembic migrations
echo "Running database migrations..."
alembic upgrade head

# Start FastAPI backend
echo "Starting backend on http://localhost:8000"
uvicorn backend.app:create_app --factory --reload --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

# Start Vite frontend dev server
echo "Starting frontend on http://localhost:5173"
cd frontend && npm run dev &
FRONTEND_PID=$!

cd ..

cleanup() {
    echo "Shutting down..."
    kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
}
trap cleanup EXIT

echo ""
echo "Development servers running:"
echo "  Frontend: http://localhost:5173 (with API proxy to backend)"
echo "  Backend:  http://localhost:8000"
echo ""
echo "Press Ctrl+C to stop."

wait
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x run-dev.sh`

- [ ] **Step 3: Verify the dev servers start in nix-shell**

Run (in nix-shell): `nix-shell --run "./run-dev.sh"`

Expected: Both servers start. Visit http://localhost:5173 in a browser — you should see the SkyNetControl page with health status from the API.

Stop with Ctrl+C.

- [ ] **Step 4: Commit**

```bash
git add run-dev.sh
git commit -m "feat: add development run script"
```

---

### Task 13: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `nix-shell --run "pytest tests/ -v"`

Expected: All tests pass.

- [ ] **Step 2: Verify Nix build**

Run: `nix-build default.nix`

Expected: Builds successfully.

- [ ] **Step 3: Verify project structure**

Run: `find . -not -path './.git/*' -not -path './node_modules/*' -not -path './.venv/*' -not -path './result*' -not -path './.superpowers/*' -not -path './frontend/node_modules/*' -not -path './frontend/dist/*' | sort`

Expected: All files from the file structure above exist.

- [ ] **Step 4: Tag the milestone**

```bash
git tag -a v0.0.1 -m "Phase 0: Project scaffolding and deployment packaging"
```
