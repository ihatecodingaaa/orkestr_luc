"""Smoke for invite URL consistency (P1.4).

Every backend invite link must come from one helper, use FRONTEND_URL (default
the real Vercel domain), the real query-param route (/?room=<sid>), and NEVER the
dead orkestr.app placeholder. Covers session/start, session/live, the invite
endpoint, the QR redirect, and a custom FRONTEND_URL override.

Runs in-process via TestClient (no server, no network, no payment).
Exit 0 = all pass, 1 = any failure.
"""
import os
import sys
from urllib.parse import quote

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import paidpath_guard  # noqa: F401  (force all real-x402 spend paths off in-process)

# Ensure the default path is exercised (no FRONTEND_URL configured).
os.environ.pop("FRONTEND_URL", None)
DEFAULT_BASE = "https://orkestr-luc.vercel.app"

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)
_failures = []


def check(label, ok, detail=""):
    if ok:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))
        _failures.append(label)


def good_invite(url: str, sid: str, base: str = DEFAULT_BASE) -> bool:
    return (isinstance(url, str)
            and "orkestr.app" not in url
            and url == f"{base}/?room={quote(sid, safe='')}")


print("Invite link consistency smoke (P1.4)")
print("=" * 60)

# 1. session/start
r = client.post("/api/session/start", json={}).json()
sid = r["session_id"]
check("start invite_url uses vercel default + /?room=", good_invite(r["invite_url"], sid),
      r["invite_url"])
check("start invite_url has no orkestr.app", "orkestr.app" not in r["invite_url"], r["invite_url"])

# 2. session/live
lr = client.post("/api/session/live", json={}).json()
lsid = lr["session_id"]
check("live invite_url uses vercel default + /?room=", good_invite(lr["invite_url"], lsid),
      lr["invite_url"])
check("live invite_url has no orkestr.app", "orkestr.app" not in lr["invite_url"], lr["invite_url"])

# 3. invite endpoint — invite_url + qr_url agree; QR encodes the invite_url.
inv = client.get(f"/api/session/{lsid}/invite").json()
check("invite endpoint invite_url matches", good_invite(inv["invite_url"], lsid), inv["invite_url"])
check("invite endpoint qr encodes the invite_url",
      quote(inv["invite_url"], safe="") in inv["qr_url"] and "api.qrserver.com" in inv["qr_url"],
      inv["qr_url"])
check("invite endpoint has no orkestr.app anywhere",
      "orkestr.app" not in inv["invite_url"] and "orkestr.app" not in inv["qr_url"], str(inv))

# 4. QR redirect endpoint -> 302 to a qrserver PNG encoding the same invite_url.
qr = client.get(f"/api/session/{lsid}/qr", follow_redirects=False)
loc = qr.headers.get("location", "")
check("qr endpoint -> 302 redirect", qr.status_code == 302, str(qr.status_code))
check("qr redirect targets qrserver + encodes invite_url",
      "api.qrserver.com" in loc and quote(inv["invite_url"], safe="") in loc, loc)
check("qr redirect has no orkestr.app", "orkestr.app" not in loc, loc)

# 5. All invite-producing endpoints agree on the SAME domain for one session.
same_domain = {
    inv["invite_url"].split("/?room=")[0],
    lr["invite_url"].split("/?room=")[0],
}
check("all live invite links share one domain", same_domain == {DEFAULT_BASE}, str(same_domain))

# 6. Custom FRONTEND_URL is honored (and still never orkestr.app).
os.environ["FRONTEND_URL"] = "https://demo.example.com/"
r2 = client.post("/api/session/start", json={}).json()
check("custom FRONTEND_URL honored (trailing slash trimmed)",
      good_invite(r2["invite_url"], r2["session_id"], base="https://demo.example.com"),
      r2["invite_url"])
os.environ.pop("FRONTEND_URL", None)

print("\n" + "=" * 60)
if _failures:
    print(f"  RESULT: {len(_failures)} FAILURE(S)")
    for f in _failures:
        print(f"    - {f}")
    sys.exit(1)
print("  RESULT: ALL PASS")
sys.exit(0)
