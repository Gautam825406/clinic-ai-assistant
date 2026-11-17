# Clinic AI Assistant — Frontend

Mobile-first dashboard for the clinic: Today list (Aaya / Nahi aaya), manual
booking, call log, callbacks, FR-17 config, numbers + OTP verification,
simulated demo call, platform admin view. Static site — **no build step, no
backend code, no secrets**.

Product: **Clinic AI Assistant**. Pilot specialty: **General / GP**.
Backend lives in `../backend/` (same repo, `clinic-ai-assistant`).

## Files

| File | What it is |
|---|---|
| `index.html` | The whole dashboard (single file, Hinglish UI) |
| `config.js` | **The one place the backend URL lives** — `window.__CLINIC_API_BASE__` |
| `vercel.json` | Static-site config for the Vercel deploy |
| `.env.example` | Documents the backend URL value (no real env vars — static site) |

## Point it at a backend

Edit `config.js` — change only the value:

```js
window.__CLINIC_API_BASE__ = 'https://<your-backend>.onrender.com';  // live
// window.__CLINIC_API_BASE__ = 'http://localhost:8000';             // local dev
// window.__CLINIC_API_BASE__ = '';                                   // same origin
```

Every API call in `index.html` goes through that one variable (the `API`
const); no backend URL is hardcoded anywhere else. API key login is stored
only in the browser's `localStorage`.

## Run locally

```bash
# 1. Start the backend first (from the repo root):
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir backend
#    (with DEMO_MODE=true SEED_DEMO=1 for the DEMO clinic)

# 2. Point the frontend at it: in config.js set
#    window.__CLINIC_API_BASE__ = 'http://localhost:8000'
#    (do NOT commit that line — keep the committed value '' and set it at deploy time)

# 3. Serve this folder:
python3 -m http.server 8080 --directory frontend
#    open http://localhost:8080 — sign up via POST /auth/signup to get an
#    api_token, paste it in the dashboard's API Key box.
```

## Deploy (Vercel, static)

Same repo, `clinic-ai-assistant`. In Vercel: **Add New → Project → import
`Gautam825406/clinic-ai-assistant` → Root Directory = `frontend`**,
Framework Preset = **Other** (static, no build command, no output directory
setting needed). Before deploying, set the backend URL in `config.js` to the
live Render backend URL (see `../backend/README.md` for that deploy).

No env vars are needed on Vercel for this project. The old Vercel Python
deploy of the backend (`api/index.py` + root `vercel.json`) was removed —
the backend deploys on Render only.

## API paths used (all via the `API` base in `index.html`)

- `GET /health`, `GET /api/open-decisions` (no auth)
- `GET /clinics/me`, `PATCH /clinics/me`
- `GET /clinics/me/today?day=…`, `GET /clinics/me/dashboard-stats?day=…`
- `GET /clinics/me/calls`, `GET /clinics/me/callbacks`, `PATCH /clinics/me/callbacks/{id}`
- `POST /clinics/me/doctors`
- `GET /slots?doctor_id=…&date=…`
- `POST /appointments`, `POST /appointments/{id}/outcome`
- `POST /numbers/request-otp`, `POST /numbers/verify-otp`, `GET /numbers/me`
- `POST /voice/simulate`
- `GET /admin/tenants`, `GET /admin/stats` (platform admin only)

Full API reference: [`../backend/docs/API.md`](../backend/docs/API.md)
(also at `docs/API.md` in the backend folder).
