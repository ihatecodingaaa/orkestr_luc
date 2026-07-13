"""Settlement agent — turns who-fronted-what into the minimal transfer set.

Owner: Jaydon. Uses the REAL algorithm in core/settlement.py.

Honesty contract: friend-to-friend legs are NEVER on-chain (friends don't hold
wallets). A transfer is plain tracked state — from/to/amount/status — settled by
whatever the friends already use (PayNow/Venmo/cash) and marked paid in the app.
No transaction hash, no latency, no chain claim of any kind.
"""
from core.settlement import compute_net, simplify


def build_settlement(fronted: dict, shares: dict) -> dict:
    net = compute_net(fronted, shares)
    transfers = [{**t, "status": "pending"} for t in simplify(net)]
    return {
        "fronted": fronted,
        "shares": shares,
        "net": net,
        "transfers": transfers,
        "net_after": {p: 0 for p in net},
    }
