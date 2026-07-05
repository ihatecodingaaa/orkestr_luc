"""SQLite-backed session store for Orkestr. Owner: Lucas.

Persists session dicts as JSON blobs so they survive server restarts, replacing
the old in-memory dict in core/session.py. A per-process in-memory cache holds
the LIVE session objects in front of SQLite, so:
  * reads are unchanged (a cache hit returns the same object reference as
    before), and
  * in-place mutations by core/session.py and the agents land on the cached
    object; core/session.py calls set_session() write-through AFTER each
    mutation to persist.

Interface (the only surface other modules use): get_session, set_session,
delete_session, list_sessions. Stdlib only — sqlite3, json, threading.
"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

# sessions.db lives in the repo root (core/../sessions.db). It is covered by the
# existing ".db" .gitignore rule, so it is never tracked.
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "sessions.db")

# One shared connection (uvicorn calls routes from a threadpool, so
# check_same_thread=False) guarded by a single lock around ALL access.
_LOCK = threading.Lock()
_CACHE = {}

_CONN = sqlite3.connect(DB_PATH, check_same_thread=False)
_CONN.execute(
    "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
# Group memory: a returning member's constraints are stored by their stable id
# (P-002 etc.) so a new session can auto-populate them. constraints is a JSON
# string; independent of the sessions table above.
_CONN.execute(
    "CREATE TABLE IF NOT EXISTS member_prefs ("
    "member_id TEXT PRIMARY KEY, name TEXT, constraints TEXT, "
    "freeform TEXT, updated_at TEXT)")
_CONN.commit()


def get_session(sid):
    """Return the live cached session dict, loading from SQLite on first access.

    Returns the SAME object on repeat calls (by reference) so the existing
    in-place mutation pattern in core/session.py keeps working unchanged.
    Returns None if the sid is unknown.
    """
    if sid in _CACHE:
        return _CACHE[sid]
    with _LOCK:
        row = _CONN.execute(
            "SELECT data FROM sessions WHERE id = ?", (sid,)).fetchone()
    if row is None:
        return None
    data = json.loads(row[0])
    _CACHE[sid] = data
    return data


def set_session(sid, data):
    """Write-through: update the cache and persist the JSON blob to SQLite."""
    blob = json.dumps(data)
    _CACHE[sid] = data
    with _LOCK:
        _CONN.execute(
            "INSERT OR REPLACE INTO sessions (id, data) VALUES (?, ?)", (sid, blob))
        _CONN.commit()
    return data


def delete_session(sid):
    """Remove a session from both the in-memory cache and SQLite."""
    _CACHE.pop(sid, None)
    with _LOCK:
        _CONN.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        _CONN.commit()


def list_sessions():
    """All session ids currently persisted in SQLite."""
    with _LOCK:
        rows = _CONN.execute("SELECT id FROM sessions").fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Group memory — persistent per-member constraints (keyed by stable member id)
# ---------------------------------------------------------------------------

def _row_to_prefs(row):
    """Shape a member_prefs row into a public dict (constraints JSON -> dict)."""
    if row is None:
        return None
    return {
        "member_id": row[0],
        "name": row[1],
        "constraints": json.loads(row[2]) if row[2] else {},
        "freeform": row[3] or "",
        "updated_at": row[4],
    }


def save_member_prefs(member_id, name, constraints_dict, freeform):
    """Upsert a member's stored constraints. Overwrites any prior row (memory is
    replaced, not merged) and stamps updated_at with the current UTC time.
    """
    blob = json.dumps(constraints_dict or {})
    updated_at = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        _CONN.execute(
            "INSERT OR REPLACE INTO member_prefs "
            "(member_id, name, constraints, freeform, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (member_id, name, blob, freeform or "", updated_at))
        _CONN.commit()
    return _row_to_prefs((member_id, name, blob, freeform or "", updated_at))


def get_member_prefs(member_id):
    """Return a member's stored prefs dict, or None if never saved."""
    with _LOCK:
        row = _CONN.execute(
            "SELECT member_id, name, constraints, freeform, updated_at "
            "FROM member_prefs WHERE member_id = ?", (member_id,)).fetchone()
    return _row_to_prefs(row)


def get_all_member_prefs():
    """All stored member pref dicts (for a future group-memory dashboard)."""
    with _LOCK:
        rows = _CONN.execute(
            "SELECT member_id, name, constraints, freeform, updated_at "
            "FROM member_prefs ORDER BY member_id").fetchall()
    return [_row_to_prefs(r) for r in rows]


def _validate_json_roundtrip():
    """Import-time guard: confirm a session-shaped dict round-trips through stdlib
    json cleanly BEFORE the refactor depends on it. Fails loud at module load
    (server won't boot) rather than mid-request if json ever can't handle the
    expected shape.
    """
    probe = {
        "session_id": "PROBE-000",
        "name": "probe",
        "date_range": ["2026-06-26", "2026-06-28"],
        "phase": "plan_ready",
        "members": [{"id": "P-001", "name": "Alice", "constraints": {}, "freeform": ""}],
        "negotiation": [{"from": "P-001", "text": "hi"}],
        "plan": {"per_person": 80, "venue": {"name": "Seoul Garden"}, "total_cost": 400},
        "handshake": {"status": "co-signed", "booking_ref": "SG-2026-0627-77"},
        "approvals": ["P-001"],
        "expenses": [{"paid_by": "P-001", "amount": 240.0}],
        "settlement": {"net": {"P-001": 0}, "transfers": []},
    }
    restored = json.loads(json.dumps(probe))
    assert restored == probe, "session-shaped dict did not round-trip through json"


_validate_json_roundtrip()
