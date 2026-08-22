# Architecture — AlertTriage

## Overview
AlertTriage is a single Streamlit app that turns a noisy stream of raw alerts into a small
number of actionable, human-readable incidents, delegating the severity judgment to the LLM
instead of a naive alert-count threshold.

## Data model
- `alerts` — every raw alert is stored as-is, tagged with `source` and a nullable
  `incident_id`. Alerts start unclustered (`incident_id = NULL`) and get assigned once
  clustering runs. Raw alerts are kept (not discarded after clustering) so every incident has an
  audit trail back to the exact log lines that triggered it.
- `incidents` — one row per LLM-generated cluster: title, summary, severity, the LLM's stated
  `severity_reason`, and Slack posting status.

## Clustering & severity logic
All unclustered alerts are sent to Claude in a single batch as JSON, with an instruction to (a)
group alerts describing the same underlying problem and (b) assign severity from content,
explicitly told not to rely on raw alert count. The model returns structured JSON (title,
summary, severity, severity_reason, alert_ids per incident), which is parsed and used to update
both tables — every alert ends up linked to exactly one incident.

**Why content-based severity matters:** three "disk usage at 40%" alerts from flaky monitoring
are noise; one "payment provider timeout rate 95%" alert is a critical incident. A pure
alert-count rule gets both of these backwards, so severity is delegated to the LLM's read of the
actual alert text, with reasoning captured in `severity_reason` for later review.

## Offline fallback
Two failure modes needed handling for a demoable project: no API key, and an LLM response that
doesn't parse as JSON. Both fall back to `offline_cluster_fallback()`, a deterministic
group-by-source heuristic, so the pipeline (ingest → cluster → post) is never blocked. The UI
surfaces when fallback mode was used so it's never silently misleading.

## Slack integration
Uses a Slack Incoming Webhook (a single POST URL) rather than the full Slack Bot API — the
minimum integration needed to "post a clean incident summary to Slack," with no OAuth flow
required. Without a webhook configured, the endpoint still runs the full formatting logic and
marks the incident as posted with a "simulated" status, so the feature is end-to-end testable
without a live Slack workspace.

## Challenges & decisions
- **Batch vs. per-alert clustering.** Clustering is one LLM call per batch (not one call per
  alert) so the model has full context to compare alerts against each other — this is what makes
  grouping possible, and it's cheaper than N separate calls.
- **Idempotency.** Once an alert is assigned an `incident_id`, it's excluded from future
  clustering runs, so re-clicking "cluster" never double-processes the same alerts.
- **JSON-only LLM output.** The prompt explicitly demands "ONLY a JSON array" and the app strips
  markdown code fences defensively, since LLMs occasionally wrap JSON in \`\`\`json blocks
  despite instructions.
- **Single-file Streamlit app.** Chosen over a separate Flask API + React frontend to make GitHub
  upload and Streamlit Cloud deployment as simple as possible for an internship-scale project —
  one file, one deploy step, no CORS/API-hosting concerns.
