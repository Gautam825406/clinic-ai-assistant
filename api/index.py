"""Vercel Python entrypoint for Clinic AI Assistant.

Vercel runs this file as a serverless function; it re-exports the FastAPI
app from backend/app/main.py so the same codebase serves the dashboard and
the API on Vercel. DATABASE_URL must point at /tmp (or Postgres) on Vercel —
set it in the project env vars; never commit real credentials.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.main import app  # noqa: E402,F401  (Vercel looks for `app`)
