/* Clinic AI Assistant — frontend config (ONE place for the backend URL).
 *
 * The dashboard is a static site: it has no server and no build step, so the
 * backend URL lives here, not in an env var.
 *
 * - Local dev:   window.__CLINIC_API_BASE__ = 'http://localhost:8000'
 * - Live deploy: window.__CLINIC_API_BASE__ = 'https://<your-backend>.onrender.com'
 *                (the Render URL, no trailing slash)
 * - Same origin (backend serves the dashboard too, old monolith layout): ''
 *
 * Change ONLY the value below when the backend moves. Everything in
 * index.html calls the backend through this one variable — no URL is
 * hardcoded anywhere else in the frontend.
 */
window.__CLINIC_API_BASE__ = 'https://clinic-ai-assistant-mljb.onrender.com';
