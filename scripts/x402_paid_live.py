#!/usr/bin/env python3
"""Trigger the protected /api/x402/paid-smoke on the deployed backend.

This makes ONE real $0.001 Base-Sepolia USDC transfer (buyer -> merchant) so the
faithful x402 settlement can be proven on-chain. It is intentionally hard to run.

SAFETY — this spends real testnet USDC. Three independent opt-ins are required:
  * env  RUN_X402_PAID_SMOKE=true          (explicit opt-in)
  * env  X402_SMOKE_SECRET=<server secret> (sent as a header; NEVER printed)
  * CLI  --confirm PAY_0.001_USDC          (exact confirmation phrase)

The server adds two more gates (feature flag + secret check + confirmation body)
and a buyer != merchant guard, so an accidental or unauthorized call cannot move
funds.

Usage (PowerShell):
    $env:BASE_URL="https://orkestr-luc.up.railway.app"   # optional; this is the default
    $env:X402_SMOKE_SECRET="<secret>"
    $env:RUN_X402_PAID_SMOKE="true"
    python scripts/x402_paid_live.py --confirm PAY_0.001_USDC

Stdlib only (urllib) — avoids importing the EVM stack locally. Never prints any
secret. Exits 0 only when ok=true AND payment_mode=x402_real.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "https://orkestr-luc.up.railway.app"
CONFIRM_PHRASE = "PAY_0.001_USDC"
PATH = "/api/x402/paid-smoke"
SECRET_HEADER = "X-Orkestr-Smoke-Secret"


def _fail(msg: str, code: int = 2):
    print(f"[x402-paid-live] ERROR: {msg}")
    sys.exit(code)


def main():
    parser = argparse.ArgumentParser(description="Trigger the protected x402 paid smoke.")
    parser.add_argument("--confirm", default="",
                        help=f'must be exactly "{CONFIRM_PHRASE}"')
    args = parser.parse_args()

    base = os.environ.get("BASE_URL", DEFAULT_BASE).rstrip("/")

    # --- client-side opt-ins (the server enforces its own gates too) ---
    if os.environ.get("RUN_X402_PAID_SMOKE", "").strip().lower() != "true":
        _fail("refusing to run: set RUN_X402_PAID_SMOKE=true to opt in")
    if args.confirm != CONFIRM_PHRASE:
        _fail(f"refusing to run: pass --confirm {CONFIRM_PHRASE}")
    secret = os.environ.get("X402_SMOKE_SECRET", "").strip()
    if not secret:
        _fail("X402_SMOKE_SECRET is not set in the local environment")

    url = base + PATH
    body = json.dumps({"confirm": CONFIRM_PHRASE}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", SECRET_HEADER: secret},
    )
    print(f"[x402-paid-live] POST {url}")
    print("[x402-paid-live] confirm phrase OK; secret header attached (value hidden)")

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            status = resp.status
            data = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            data = json.loads(e.read() or b"{}")
        except Exception:
            data = {}
    except Exception as exc:
        _fail(f"request failed: {exc.__class__.__name__}")
        return  # unreachable; keeps linters happy

    if not isinstance(data, dict):
        data = {}

    # The server response is already sanitized (no secrets). Print it as-is.
    print(f"[x402-paid-live] HTTP {status}")
    print(json.dumps(data, indent=2))

    tx = data.get("txHash")
    if tx:
        explorer = data.get("blockExplorerUrl") or ("https://sepolia.basescan.org/tx/" + str(tx))
        print(f"[x402-paid-live] txHash:   {tx}")
        print(f"[x402-paid-live] Basescan: {explorer}")

    frm, to = data.get("from"), data.get("to")
    if frm and to:
        print(f"[x402-paid-live] from: {frm}")
        print(f"[x402-paid-live] to:   {to}")
        print(f"[x402-paid-live] from != to: {str(frm).lower() != str(to).lower()}")

    ok = bool(data.get("ok")) and data.get("payment_mode") == "x402_real"
    if ok:
        print("[x402-paid-live] RESULT: PAID (x402_real)")
        sys.exit(0)
    print("[x402-paid-live] RESULT: NOT a confirmed real payment "
          f"(ok={data.get('ok')}, payment_mode={data.get('payment_mode')}, "
          f"error={data.get('error')})")
    sys.exit(1)


if __name__ == "__main__":
    main()
