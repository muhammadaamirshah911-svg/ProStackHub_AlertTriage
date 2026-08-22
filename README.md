# AlertTriage — ProStackHub LLM & AI Automation Internship (Task 4)

Ingests a stream of raw system alerts (simulated from logs), clusters related alerts into one
summarized incident using an LLM, assigns severity based on content and history — not just alert
count — and posts a clean incident summary to Slack, replacing a wall of raw alerts.

## Features
- **Simulated alert stream** — one click generates realistic raw alerts (API errors, DB issues,
  disk usage, etc.) as a stand-in for a real monitoring feed.
- **LLM clustering** — sends the batch of raw alerts to Claude, which groups related ones into
  incidents and writes a human-readable title + summary for each.
- **Content-aware severity** — severity (critical/warning/info) is decided by the LLM based on
  what the alerts actually describe, with a stated reason — not simply "3+ alerts = critical."
- **Slack posting** — posts a formatted incident summary via a Slack Incoming Webhook. Runs in a
  simulated mode (still fully functional in the UI) if no webhook is configured.
- **Offline fallback** — if no `ANTHROPIC_API_KEY` is set, a heuristic clustering fallback keeps
  the whole app demoable end-to-end.

## Tech stack
- Python + Streamlit (single-file app, `app.py`)
- SQLite for storage
- Anthropic API (Claude) for clustering
- Slack Incoming Webhooks for posting

## Run locally
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key_here        # optional — offline heuristic fallback otherwise
export SLACK_WEBHOOK_URL=your_webhook_here    # optional — simulated posting otherwise
streamlit run app.py
```
Opens at `http://localhost:8501`.

To get a `SLACK_WEBHOOK_URL`: create a Slack app at api.slack.com/apps → Incoming Webhooks →
Add New Webhook to Workspace → copy the URL.

## Deploy (free, Streamlit Community Cloud)
1. Push this repo to GitHub (public).
2. Go to [share.streamlit.io](https://share.streamlit.io) → "New app" → connect this repo →
   main file path `app.py`.
3. In **Settings → Secrets**, add:
   ```
   ANTHROPIC_API_KEY = "your_key_here"
   SLACK_WEBHOOK_URL = "your_webhook_here"
   ```
4. Deploy — you'll get a live `https://<something>.streamlit.app` link.

## Demo flow
1. Click **"Simulate incoming alerts"** to generate ~10 raw alerts.
2. Click **"Cluster pending alerts into incidents"** — the LLM groups them and assigns severity.
3. Click **"Post summary to Slack"** on any incident to send it (or simulate sending it).

## Deliverables checklist (per task brief)
- [x] GitHub repository — public, clean code, this README
- [ ] Live demo link — add after deploying
- [ ] Demo video (2–3 min)
- [x] Technical write-up — see `ARCHITECTURE.md`
