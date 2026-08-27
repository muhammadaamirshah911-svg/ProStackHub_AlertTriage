"""
AlertTriage — ProStackHub LLM & AI Automation Internship (Task 4)

Ingests a stream of raw system alerts (simulated from logs), clusters related
alerts into one summarized incident using an LLM, assigns severity based on
content/history (not just alert count), and posts a clean incident summary
to Slack, replacing a wall of raw alerts.

Run locally:
    pip install -r requirements.txt
    streamlit run app.py

Deploy: push this repo to GitHub, then deploy free on share.streamlit.io
(Streamlit Community Cloud) — set ANTHROPIC_API_KEY (and optionally
SLACK_WEBHOOK_URL) in the app's Secrets.
"""
import os
import json
import random
import sqlite3
from datetime import datetime, timedelta

import streamlit as st
import requests

DB_PATH = os.path.join(os.path.dirname(__file__), "alerttriage.db")
ANTHROPIC_MODEL = "claude-sonnet-4-6"

SEVERITY_EMOJI = {"critical": "🔴", "warning": "🟡", "info": "🔵"}

SAMPLE_ALERT_TEMPLATES = [
    ("api-gateway", "5xx error rate spiked to {pct}% on /checkout endpoint"),
    ("api-gateway", "5xx error rate spiked to {pct}% on /payments endpoint"),
    ("db-primary", "connection pool exhausted, {n} queries queued"),
    ("db-primary", "replication lag reached {n}s on read replica"),
    ("auth-service", "login failure rate up {pct}% in last 5 min"),
    ("cache-layer", "Redis memory usage at {pct}%, evictions increasing"),
    ("worker-queue", "job queue backlog at {n} pending jobs"),
    ("cdn", "origin fetch latency p99 at {n}ms"),
    ("payments-service", "3rd party payment provider timeout rate {pct}%"),
    ("disk", "disk usage on node-{n} at {pct}%"),
]


def get_secret(key):
    try:
        if hasattr(st, "secrets") and key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.environ.get(key, "")


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_text TEXT NOT NULL,
            source TEXT,
            received_at TEXT NOT NULL,
            incident_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            severity TEXT NOT NULL,
            severity_reason TEXT,
            alert_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            posted_to_slack INTEGER DEFAULT 0,
            slack_response TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def simulate_alerts(count=8):
    conn = get_conn()
    now = datetime.utcnow()
    for _ in range(count):
        source, template = random.choice(SAMPLE_ALERT_TEMPLATES)
        text = template.format(pct=random.randint(15, 95), n=random.randint(5, 500))
        ts = (now - timedelta(seconds=random.randint(0, 300))).isoformat()
        conn.execute("INSERT INTO alerts (raw_text, source, received_at) VALUES (?, ?, ?)", (text, source, ts))
    conn.commit()
    conn.close()


def get_unclustered_alerts():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM alerts WHERE incident_id IS NULL ORDER BY received_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_incidents():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM incidents ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# LLM clustering
# ---------------------------------------------------------------------------
def call_llm_json(prompt_text):
    api_key = get_secret("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": ANTHROPIC_MODEL, "max_tokens": 1500,
              "messages": [{"role": "user", "content": prompt_text}]},
        timeout=60,
    )
    data = resp.json()
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def offline_cluster_fallback(alerts):
    by_source = {}
    for a in alerts:
        by_source.setdefault(a["source"], []).append(a)
    incidents = []
    for source, group in by_source.items():
        severity = "critical" if len(group) >= 3 else ("warning" if len(group) == 2 else "info")
        incidents.append({
            "title": f"Anomalies detected in {source}",
            "summary": f"{len(group)} related alert(s) from {source}: " + "; ".join(a["raw_text"] for a in group[:3]),
            "severity": severity,
            "severity_reason": f"Grouped by source ({source}); {len(group)} alerts (offline heuristic — no LLM key configured).",
            "alert_ids": [a["id"] for a in group],
        })
    return incidents


def run_clustering():
    alerts = get_unclustered_alerts()
    if not alerts:
        return [], False

    prompt = f"""You are an SRE on-call assistant. Below is a batch of raw system alerts (JSON array).
Group related alerts into incidents (alerts about the same underlying problem belong together,
even if wording differs). For each incident, assign a severity of "critical", "warning", or "info"
based on the actual content and how many alerts support it — not just a raw alert count.

Raw alerts:
{json.dumps(alerts, indent=2)}

Respond with ONLY a JSON array, no other text, in this exact shape:
[
  {{
    "title": "short incident title",
    "summary": "1-2 sentence summary of what's happening and likely impact",
    "severity": "critical" | "warning" | "info",
    "severity_reason": "why this severity was chosen",
    "alert_ids": [list of the integer ids from the input that belong to this incident]
  }}
]"""

    incidents_data = call_llm_json(prompt)
    used_fallback = incidents_data is None
    if used_fallback:
        incidents_data = offline_cluster_fallback(alerts)

    conn = get_conn()
    now = datetime.utcnow().isoformat()
    for inc in incidents_data:
        alert_ids = inc.get("alert_ids", [])
        cur = conn.execute(
            "INSERT INTO incidents (title, summary, severity, severity_reason, alert_count, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (inc["title"], inc["summary"], inc["severity"], inc.get("severity_reason", ""), len(alert_ids), now),
        )
        incident_id = cur.lastrowid
        for aid in alert_ids:
            conn.execute("UPDATE alerts SET incident_id=? WHERE id=?", (incident_id, aid))
    conn.commit()
    conn.close()
    return incidents_data, used_fallback


def post_to_slack(incident):
    webhook = get_secret("SLACK_WEBHOOK_URL")
    emoji = SEVERITY_EMOJI.get(incident["severity"], "⚪")
    text = (f"{emoji} *[{incident['severity'].upper()}] {incident['title']}*\n{incident['summary']}\n"
            f"_Based on {incident['alert_count']} clustered alert(s). {incident['severity_reason']}_")
    if webhook:
        resp = requests.post(webhook, json={"text": text}, timeout=15)
        status = f"Posted (HTTP {resp.status_code})"
    else:
        status = "SLACK_WEBHOOK_URL not configured — simulated post only"

    conn = get_conn()
    conn.execute("UPDATE incidents SET posted_to_slack=1, slack_response=? WHERE id=?", (status, incident["id"]))
    conn.commit()
    conn.close()
    return status, text


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------
st.set_page_config(page_title="AlertTriage — ProStackHub", page_icon="🚨", layout="wide")
init_db()

st.title("🚨 AlertTriage")
st.caption("Cluster raw system alerts into incidents · content-based severity · Slack-ready summaries")

llm_ok = bool(get_secret("ANTHROPIC_API_KEY"))
slack_ok = bool(get_secret("SLACK_WEBHOOK_URL"))
status_cols = st.columns(2)
status_cols[0].info(f"LLM: {'connected ✅' if llm_ok else 'demo mode (no key)'}")
status_cols[1].info(f"Slack: {'configured ✅' if slack_ok else 'simulated (no webhook)'}")

st.divider()

col1, col2 = st.columns(2)
with col1:
    count = st.number_input("How many alerts to simulate", min_value=1, max_value=30, value=10)
    if st.button("📥 Simulate incoming alerts"):
        simulate_alerts(count)
        st.rerun()
with col2:
    pending = get_unclustered_alerts()
    if st.button(f"🧠 Cluster {len(pending)} pending alert(s) into incidents", disabled=len(pending) == 0, type="primary"):
        with st.spinner("Calling LLM to cluster alerts..."):
            incidents_data, used_fallback = run_clustering()
        if used_fallback:
            st.warning("Clustered using offline heuristic (no ANTHROPIC_API_KEY set). Add a key for real LLM clustering.")
        else:
            st.success(f"Clustered into {len(incidents_data)} incident(s).")
        st.rerun()

st.subheader(f"Raw Alert Stream ({len(pending)} unclustered)")
st.caption("Alerts are simulated from logs for demo purposes.")
if pending:
    st.dataframe(
        [{"source": a["source"], "alert": a["raw_text"], "received_at": a["received_at"]} for a in pending],
        use_container_width=True, hide_index=True,
    )
else:
    st.info("No unclustered alerts. Click 'Simulate incoming alerts' to generate a demo batch.")

st.divider()
st.subheader("Clustered Incidents")
incidents = get_incidents()
if not incidents:
    st.info("No incidents yet. Cluster some alerts first.")
for inc in incidents:
    emoji = SEVERITY_EMOJI.get(inc["severity"], "⚪")
    with st.container(border=True):
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"### {emoji} {inc['title']}")
        c2.markdown(f"**{inc['severity'].upper()}**")
        st.write(inc["summary"])
        st.caption(inc["severity_reason"])
        st.caption(f"{inc['alert_count']} alert(s) clustered · {inc['created_at']}")
        if inc["posted_to_slack"]:
            st.success(f"Posted to Slack ✓ — {inc['slack_response']}")
        else:
            if st.button("📤 Post summary to Slack", key=f"slack_{inc['id']}"):
                status, text = post_to_slack(inc)
                st.info(status)
                st.code(text, language=None)
                st.rerun()
