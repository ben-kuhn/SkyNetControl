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
