"""Venue agent — THE OTHER SIDE of the handshake. Owner: Lucas.

Checks availability against venue attributes, quotes/counters terms, signs the cart.
Currently returns seeded responses; wire the real matching logic in Phase 2.
"""
import json
import os

_VENUES = None


def _load():
    global _VENUES
    if _VENUES is None:
        path = os.path.join(os.path.dirname(__file__), "..", "data", "venues.json")
        with open(path) as f:
            _VENUES = json.load(f)
    return _VENUES


def check_availability(request: dict) -> dict:
    """SANDBOX ONLY — no real availability system is queried.

    Returns a SUGGESTED time so the demo flow has a coherent shape; it is not a
    held slot and must never be presented as a real reservation. Live rooms use
    the Merchant Adapter handoff (Maps/website/phone) to actually reserve.
    """
    return {
        "available": None,  # unknown — the sandbox does not check real inventory
        "time_offered": "19:30",  # suggested time, not a held reservation slot
        "time_is_suggestion": True,
        "price_total": 240,
        "per_head": 48,
        "meets": ["halal", "vegetarian_option"],
    }


def sign_cart(cart: dict, merchant: str = "Seoul Garden") -> str:
    from payments.mandate import sign_cart as _sign
    return _sign(cart, merchant)
