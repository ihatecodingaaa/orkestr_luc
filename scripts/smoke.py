"""Smoke tests for the Orkestr backend hardening.

Stdlib only (urllib.request, json, os, sys). No pytest, no third-party deps.

Usage:
    python scripts/smoke.py                              # targets http://localhost:8000
    BASE_URL=https://your-app.vercel.app python scripts/smoke.py
    BASE_URL=http://localhost:8000 python scripts/smoke.py

Exit code 0 = all pass, 1 = any failure.
The server must already be running; this script does not start uvicorn.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
# POST /api/dev/reseed is gated (P0.3): it needs ENABLE_DEV_RESEED=true on the
# server and the matching secret in the X-Orkestr-Reseed-Secret header. Run this
# smoke with both env vars set (see scripts docstring / README env checklist).
RESEED_SECRET = os.environ.get("DEV_RESEED_SECRET", "")
_failures = []

print(f"Orkestr smoke tests — target: {BASE}")
print("=" * 60)


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  [PASS] {label}")
    else:
        msg = f"  [FAIL] {label}" + (f" — {detail}" if detail else "")
        print(msg)
        _failures.append(label)


def http(method: str, path: str, body=None):
    """Return (status_code, parsed_body). body=None for GET/no-payload POST."""
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, None


def reseed():
    """POST /api/dev/reseed with the gate secret header (P0.3). Returns (status, body)."""
    url = BASE + "/api/dev/reseed"
    req = urllib.request.Request(
        url, data=b"", method="POST",
        headers={"X-Orkestr-Reseed-Secret": RESEED_SECRET})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, None


def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Reseed gate (P0.3): with the feature enabled, a WRONG secret must 403 and must
# NOT reseed; the correct secret (sent by reseed()) must 200. This is the local
# validation path — run with ENABLE_DEV_RESEED=true + DEV_RESEED_SECRET set.
# (Feature-disabled -> 404 is covered by manual QA, since this run enables it.)
# ---------------------------------------------------------------------------
_bad = urllib.request.Request(
    BASE + "/api/dev/reseed", data=b"", method="POST",
    headers={"X-Orkestr-Reseed-Secret": "definitely-wrong-secret"})
try:
    with urllib.request.urlopen(_bad) as _r:
        _gate_status = _r.status
except urllib.error.HTTPError as _e:
    _gate_status = _e.code
check("POST /api/dev/reseed wrong secret -> 403 (gate holds)",
      _gate_status == 403, f"got {_gate_status}")

# ---------------------------------------------------------------------------
# Ensure pristine state before starting
# ---------------------------------------------------------------------------
_seed_status, _ = reseed()
check("POST /api/dev/reseed correct secret -> 200 (reseeded)",
      _seed_status == 200, f"got {_seed_status} — is ENABLE_DEV_RESEED=true and DEV_RESEED_SECRET set?")

# ---------------------------------------------------------------------------
section("READ — valid ORK-001")
# ---------------------------------------------------------------------------

status, body = http("GET", "/health")
check("GET /health -> {ok:true}", status == 200 and body == {"ok": True}, str(body))

status, body = http("GET", "/api/status/ORK-001")
check("GET /api/status/ORK-001 -> plan_ready",
      status == 200 and body.get("phase") == "plan_ready", str(body))

status, body = http("GET", "/api/negotiation/ORK-001")
check("GET /api/negotiation/ORK-001 -> 10 messages",
      status == 200 and isinstance(body, list) and len(body) == 10,
      f"got {len(body) if isinstance(body, list) else body}")

status, body = http("GET", "/api/plan/ORK-001")
check("GET /api/plan/ORK-001 -> per_person=80",
      status == 200 and body.get("per_person") == 80, str(body.get("per_person")))
check("GET /api/plan/ORK-001 -> venue Seoul Garden",
      status == 200 and (body.get("venue") or {}).get("name") == "Seoul Garden",
      str((body.get("venue") or {}).get("name")))

status, body = http("GET", "/api/handshake/ORK-001")
check("GET /api/handshake/ORK-001 -> status co-signed",
      status == 200 and body.get("status") == "co-signed", str(body.get("status")))
check("GET /api/handshake/ORK-001 -> merchant Seoul Garden",
      status == 200 and body.get("merchant") == "Seoul Garden", str(body.get("merchant")))

status, body = http("GET", "/api/expenses/ORK-001")
check("GET /api/expenses/ORK-001 -> 3 expenses",
      status == 200 and isinstance(body, list) and len(body) == 3,
      f"got {len(body) if isinstance(body, list) else body}")

status, body = http("GET", "/api/settlement/ORK-001")
transfers = body.get("transfers", []) if isinstance(body, dict) else []
net = body.get("net", {}) if isinstance(body, dict) else {}
check("GET /api/settlement/ORK-001 -> 3 transfers",
      status == 200 and len(transfers) == 3, f"got {len(transfers)}")
check("GET /api/settlement/ORK-001 -> sum(net)==0",
      status == 200 and sum(net.values()) == 0,
      f"sum={sum(net.values()) if net else 'n/a'}")

# ---------------------------------------------------------------------------
section("UNKNOWN-SESSION — all must return 404")
# ---------------------------------------------------------------------------

for endpoint in [
    "/api/status/NOPE",
    "/api/negotiation/NOPE",
    "/api/plan/NOPE",
    "/api/handshake/NOPE",
    "/api/expenses/NOPE",
    "/api/settlement/NOPE",
]:
    status, body = http("GET", endpoint)
    check(f"GET {endpoint} -> 404",
          status == 404, f"got {status} body={body}")

# ---------------------------------------------------------------------------
section("VALID-EMPTY-EXPENSES — no compute_plan side effect")
# ---------------------------------------------------------------------------

status, body = http("POST", "/api/session/start", {})
scratch = (body or {}).get("session_id", "")
check("POST /api/session/start -> session_id",
      status == 200 and bool(scratch), str(body))
if scratch:
    status, body = http("GET", f"/api/expenses/{scratch}")
    check(f"GET /api/expenses/{{scratch}} -> [] (200)",
          status == 200 and body == [], f"got status={status} body={body}")

# ---------------------------------------------------------------------------
section("CONSTRAINTS FAILURE BATTERY")
# ---------------------------------------------------------------------------

status, body = http("POST", "/api/constraints", {})
check("POST /api/constraints {} -> 400 (session_id required)",
      status == 400, f"got {status}")

status, body = http("POST", "/api/constraints", {"session_id": "NOPE", "id": "P-002"})
check("POST /api/constraints {session_id:NOPE} -> 404",
      status == 404, f"got {status}")

status, body = http("POST", "/api/constraints", {"session_id": "ORK-001"})
check("POST /api/constraints {session_id:ORK-001, no id} -> 400 (id required)",
      status == 400, f"got {status}")

# ---------------------------------------------------------------------------
section("APPROVE FLOW with unknown-member guard")
# ---------------------------------------------------------------------------

reseed()

status, body = http("POST", "/api/approve/ORK-001", {"person_id": "P-999"})
check("POST /api/approve/ORK-001 {P-999} -> 400 (unknown member)",
      status == 400, f"got {status} body={body}")

status, body = http("GET", "/api/status/ORK-001")
check("GET /api/status/ORK-001 still plan_ready after rejected approve",
      status == 200 and body.get("phase") == "plan_ready",
      f"got phase={body.get('phase') if body else body}")

status, body = http("POST", "/api/approve/ORK-001", {"person_id": "P-002"})
check("POST /api/approve/ORK-001 {P-002} -> 200",
      status == 200, f"got {status}")

status, body = http("GET", "/api/status/ORK-001")
check("GET /api/status/ORK-001 -> confirmed after P-002 approves",
      status == 200 and body.get("phase") == "confirmed",
      f"got phase={body.get('phase') if body else body}")

# ---------------------------------------------------------------------------
section("SETTLE VALIDATION BATTERY — all must 4xx, settlement must NOT mutate")
# ---------------------------------------------------------------------------

reseed()

_, settlement_before = http("GET", "/api/settlement/ORK-001")

status, body = http("POST", "/api/settle/ORK-001", {})
check("POST /api/settle/ORK-001 {} -> 400",
      status == 400, f"got {status}")

status, body = http("POST", "/api/settle/ORK-001", {"person_id": "P-999"})
check("POST /api/settle/ORK-001 {person_id:P-999} -> 400",
      status == 400, f"got {status}")

status, body = http("POST", "/api/settle/ORK-001", {"fronted": "nope"})
check('POST /api/settle/ORK-001 {fronted:"nope"} -> 400',
      status == 400, f"got {status}")

status, body = http("POST", "/api/settle/ORK-001", {"fronted": {"P-999": 50}})
check("POST /api/settle/ORK-001 {fronted:{P-999:50}} -> 400",
      status == 400, f"got {status}")

status, body = http("POST", "/api/settle/ORK-001", {"fronted": {"P-001": -5}})
check("POST /api/settle/ORK-001 {fronted:{P-001:-5}} -> 400",
      status == 400, f"got {status}")

status, body = http("POST", "/api/settle/NOPE", {"person_id": "P-002"})
check("POST /api/settle/NOPE -> 404",
      status == 404, f"got {status}")

_, settlement_after = http("GET", "/api/settlement/ORK-001")
check("Settlement unchanged after validation battery",
      settlement_before == settlement_after,
      "settlement mutated during validation battery")

# ---------------------------------------------------------------------------
section("SETTLE IDEMPOTENCY")
# ---------------------------------------------------------------------------

reseed()

status, body = http("POST", "/api/settle/ORK-001", {"person_id": "P-004"})
check("POST /api/settle/ORK-001 {P-004} -> 200",
      status == 200, f"got {status}")

status, body = http("GET", "/api/settlement/ORK-001")
transfers = (body or {}).get("transfers", [])
p004_transfers = [t for t in transfers if t.get("from") == "P-004"]
check("P-004 transfer status=marked_paid after mark-as-paid",
      p004_transfers and p004_transfers[0].get("status") == "marked_paid",
      str(p004_transfers))
check("P-004 transfer records marked_by",
      p004_transfers and p004_transfers[0].get("marked_by") == "P-004",
      str(p004_transfers))
first_marked_at = p004_transfers[0].get("marked_at") if p004_transfers else None
check("P-004 transfer records marked_at timestamp",
      bool(first_marked_at), f"got {first_marked_at}")
# Honesty contract: friend legs are NEVER on-chain — no fabricated hash/latency.
check("no transfer carries tx_hash/latency_ms (no fabricated chain proof)",
      all("tx_hash" not in t and "latency_ms" not in t for t in transfers),
      str(transfers))

status, body = http("POST", "/api/settle/ORK-001", {"person_id": "P-004"})
check("POST /api/settle/ORK-001 {P-004} again -> 200 (idempotent)",
      status == 200, f"got {status}")

status, body = http("GET", "/api/settlement/ORK-001")
transfers = (body or {}).get("transfers", [])
p004_transfers = [t for t in transfers if t.get("from") == "P-004"]
second_marked_at = p004_transfers[0].get("marked_at") if p004_transfers else None
check("P-004 marked_at unchanged after second mark (idempotent)",
      first_marked_at is not None and first_marked_at == second_marked_at,
      f"first={first_marked_at} second={second_marked_at}")

# ---------------------------------------------------------------------------
section("OTHER FAIL BATTERY")
# ---------------------------------------------------------------------------

status, body = http("POST", "/api/approve/ORK-001", {})
check("POST /api/approve/ORK-001 {} -> 400 (person_id required)",
      status == 400, f"got {status}")

status, body = http("POST", "/api/expense/ORK-001", {"paid_by": "P-999", "amount": 50})
check("POST /api/expense/ORK-001 {paid_by:P-999} -> 400 (not a member)",
      status == 400, f"got {status}")

status, body = http("POST", "/api/expense/ORK-001", {"paid_by": "P-001", "amount": "lots"})
check('POST /api/expense/ORK-001 {amount:"lots"} -> 400 (not numeric)',
      status == 400, f"got {status}")

# ---------------------------------------------------------------------------
section("X402 DEMO-RECEIPT — read endpoints + gate (never fires a payment)")
# ---------------------------------------------------------------------------
# These exercise the presenter-only fresh-proof surface WITHOUT spending USDC.
# The run check uses a deliberately-wrong secret so no real payment can fire.

# CHECK A — status endpoint answers with the presenter-panel status payload.
# NOTE: the payload has no "status" key; its fields are enabled/cooldown_*/amount/
# network/etc. We assert on "enabled" (a real field) rather than a non-existent one.
status, body = http("GET", "/api/x402/demo-receipt/status")
check("GET /api/x402/demo-receipt/status -> 200 + enabled field",
      status == 200 and isinstance(body, dict) and "enabled" in body,
      f"got status={status} body={body}")

# CHECK B — latest endpoint returns a receipt carrying a txHash (saved fallback
# when no fresh proof has fired this process).
status, body = http("GET", "/api/x402/demo-receipt/latest")
check("GET /api/x402/demo-receipt/latest -> 200 + txHash field",
      status == 200 and isinstance(body, dict) and bool(body.get("txHash")),
      f"got status={status} body={body}")

# CHECK C — the gate rejects a wrong secret and NEVER fires a payment. Gate order
# is 404 (feature disabled) BEFORE 403 (bad secret), so in the default demo config
# (ENABLE_X402_DEMO_RECEIPT unset) this returns 404; with the feature enabled it
# returns 403. Both prove the gate blocks a bad secret — a 200/500/502 here would
# be the failure. We accept {403, 404}; anything else fails.
status, body = http("POST", "/api/x402/demo-receipt/run",
                    {"confirm": "PAY_0.001_USDC"})
check("POST /api/x402/demo-receipt/run (wrong/no secret) -> 403 or 404 (gate holds)",
      status in (403, 404), f"got status={status} body={body}")

# ---------------------------------------------------------------------------
section("RESET + RE-PROVE")
# ---------------------------------------------------------------------------

reseed()
status, body = http("GET", "/api/settlement/ORK-001")
transfers = (body or {}).get("transfers", [])
net = (body or {}).get("net", {})
check("After reseed: 3 transfers",
      status == 200 and len(transfers) == 3, f"got {len(transfers)}")
check("After reseed: all transfers pending",
      all(t.get("status") == "pending" for t in transfers),
      str([(t.get("from"), t.get("status")) for t in transfers]))
check("After reseed: no transfer carries tx_hash/latency_ms",
      all("tx_hash" not in t and "latency_ms" not in t for t in transfers),
      str(transfers))
check("After reseed: sum(net)==0",
      sum(net.values()) == 0, f"sum={sum(net.values()) if net else 'n/a'}")
check("After reseed: negotiation still 10 messages",
      *((lambda st, b: (st == 200 and isinstance(b, list) and len(b) == 10,
                        f"got {len(b) if isinstance(b, list) else b}")
         )(*http("GET", "/api/negotiation/ORK-001"))))
check("After reseed: plan per_person=80",
      *((lambda st, b: (st == 200 and b.get("per_person") == 80,
                        str(b.get("per_person")))
         )(*http("GET", "/api/plan/ORK-001"))))

# ---------------------------------------------------------------------------
# Final reseed so ORK-001 ends pristine
# ---------------------------------------------------------------------------
reseed()

# ---------------------------------------------------------------------------
print(f"\n{'='*60}")
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    print(f"{'='*60}")
    sys.exit(1)
else:
    total = sum(1 for line in open(__file__) if "check(" in line)
    print(f"  RESULT: ALL PASS")
    print(f"{'='*60}")
    sys.exit(0)
