"""Offline smoke: NO fabricated settlement transaction hashes anywhere (Stage A).

The governing rule: nothing displayed may assert a fact the system did not
establish. Friend-to-friend legs are never on-chain (friends hold no wallets), so:
  (a) no transfer object carries tx_hash / latency_ms / a chain rail claim;
  (b) no code path in the settlement/payment/API layers generates a random hex
      presented as a transaction (repo-wide grep for getrandbits / stamp());
  (c) transfer statuses are ONLY "pending" or "marked_paid" (+ marked_by/marked_at);
  (d) the REAL merchant x402 receipt keeps its genuine hash field, and the mock
      demo-receipt fallback carries NO hash at all (txHash null).

Stdlib only, no server needed. Exit 0 = all pass, 1 = any failure.
"""
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import paidpath_guard  # noqa: F401  (force all real-x402 spend paths off in-process)

from core import db, session as S

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_failures = []

FORBIDDEN_TRANSFER_KEYS = ("tx_hash", "latency_ms", "rail")
ALLOWED_STATUSES = {"pending", "marked_paid"}


def check(label, ok, detail=""):
    if ok:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
        _failures.append(label)


def assert_honest_transfers(name, transfers):
    check(f"[{name}] no transfer carries {FORBIDDEN_TRANSFER_KEYS}",
          all(k not in t for t in transfers for k in FORBIDDEN_TRANSFER_KEYS),
          str(transfers))
    check(f"[{name}] statuses only pending/marked_paid",
          all(t.get("status") in ALLOWED_STATUSES for t in transfers),
          str([t.get("status") for t in transfers]))


print("No-fake-hashes smoke (offline)")
print("=" * 60)

# ---------------------------------------------------------------------------
# (b) Repo scan: the settlement/payment/agent/API layers must contain no random
# hex generator presented as a tx. payments/x402.py (the old mock) must be gone.
# ---------------------------------------------------------------------------
scan_dirs = ["core", "agents", "payments", "api"]
offenders = []
for d in scan_dirs:
    for root, _dirs, files in os.walk(os.path.join(REPO, d)):
        if "__pycache__" in root:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            with open(path, encoding="utf-8", errors="replace") as f:
                src = f.read()
            if "getrandbits" in src or re.search(r"\bdef stamp\(", src):
                offenders.append(os.path.relpath(path, REPO))
check("repo scan: no getrandbits / stamp() in core|agents|payments|api",
      not offenders, str(offenders))
check("payments/x402.py (fake-hash mock) is deleted",
      not os.path.exists(os.path.join(REPO, "payments", "x402.py")))

# ---------------------------------------------------------------------------
# (a) + (c) Fresh session: transfers are honest tracked state, mark-as-paid
# records who/when, idempotently — never a hash.
# ---------------------------------------------------------------------------
SID = "TEST-NFH-1"
db.set_session(SID, {
    "session_id": SID, "name": SID, "phase": "plan_ready",
    "members": [{"id": m} for m in ("A", "B", "C")],
    "negotiation": [], "plan": None, "handshake": None,
    "approvals": [], "expenses": [{"paid_by": "A", "amount": 90}],
    "settlement": None,
})
try:
    st = S.recompute_settlement(SID)
    transfers = st["transfers"]
    check("[fresh] transfers exist", bool(transfers), "no transfers computed")
    assert_honest_transfers("fresh", transfers)
    check("[fresh] all pending before anyone marks paid",
          all(t.get("status") == "pending" for t in transfers),
          str(transfers))

    payer = transfers[0]["from"]
    S.mark_transfer_paid(SID, payer)
    st = db.get_session(SID)["settlement"]
    marked = [t for t in st["transfers"] if t["from"] == payer]
    assert_honest_transfers("marked", st["transfers"])
    check("[marked] status is marked_paid",
          all(t.get("status") == "marked_paid" for t in marked), str(marked))
    check("[marked] records marked_by == the payer",
          all(t.get("marked_by") == payer for t in marked), str(marked))
    first_at = marked[0].get("marked_at")
    check("[marked] records marked_at timestamp", bool(first_at), str(marked))

    S.mark_transfer_paid(SID, payer)  # idempotent — never re-stamps the time
    st2 = db.get_session(SID)["settlement"]
    marked2 = [t for t in st2["transfers"] if t["from"] == payer]
    check("[marked] second mark keeps original marked_at (idempotent)",
          marked2 and marked2[0].get("marked_at") == first_at,
          f"first={first_at} second={marked2[0].get('marked_at') if marked2 else None}")
finally:
    db.delete_session(SID)

# ---------------------------------------------------------------------------
# ORK-001: the seeded demo keeps its 3-transfer net-zero story — honestly.
# ---------------------------------------------------------------------------
S.seed_demo()  # idempotent; no-ops if ORK-001 already exists
ork = db.get_session("ORK-001")
ork_st = (ork or {}).get("settlement") or S.recompute_settlement("ORK-001")
ork_transfers = ork_st.get("transfers") or []
check("[ORK-001] still 3 transfers", len(ork_transfers) == 3, str(len(ork_transfers)))
check("[ORK-001] net sums to zero (cent-exact story intact)",
      round(sum((ork_st.get("net") or {}).values()), 2) == 0,
      str(ork_st.get("net")))
assert_honest_transfers("ORK-001", ork_transfers)

# ---------------------------------------------------------------------------
# (d) The REAL merchant x402 receipt keeps its genuine hash; the mock fallback
# carries NO hash at all.
# ---------------------------------------------------------------------------
from payments import x402_demo_receipt as XDR

latest = XDR.latest_receipt()
if isinstance(latest, dict) and latest:
    tx = latest.get("txHash")
    check("[merchant] latest receipt still carries a real tx hash field",
          isinstance(tx, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", tx or "") is not None,
          f"txHash={tx!r}")
else:
    check("[merchant] latest receipt present", False, f"latest={latest!r}")

from api.main import _demo_receipt_mock_fallback

mock_body = _demo_receipt_mock_fallback("timeout")
check("[mock fallback] txHash is null (no fabricated placeholder hex)",
      mock_body.get("txHash") is None, str(mock_body.get("txHash")))
check("[mock fallback] no explorer link claimed",
      mock_body.get("blockExplorerUrl") is None and mock_body.get("explorer_url") is None,
      f"{mock_body.get('blockExplorerUrl')}/{mock_body.get('explorer_url')}")
check("[mock fallback] labelled payment_mode=mock",
      mock_body.get("payment_mode") == "mock", str(mock_body.get("payment_mode")))

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
