"""AP2-shaped mandate mock. Owner: Jaydon.

The merchant signs the cart FIRST (its price/terms guarantee), then the buyer
authorizes. TODO (production): real AP2 signed mandates (ECDSA P-256).
"""
import hashlib
import json


def _cart_digest(items) -> str:
    """SHA-256 over the canonical JSON of the cart items.

    Canonicalization is sorted keys + tight separators, so the digest is stable
    regardless of key order but changes if any item value (e.g. an amount) is
    altered. verify() recomputes this exact form over mandate["items"].
    """
    canonical = json.dumps(items, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def sign_cart(cart: dict, merchant: str) -> str:
    """Venue agent signs the cart -> merchant_signature (mock)."""
    return "0xVENUE_" + merchant.replace(" ", "_").upper()[:12]


def authorize(cart_id: str, buyer: str = "convener") -> str:
    """Buyer authorizes payment -> buyer_authorization (mock)."""
    return "0xCONVENER_" + cart_id


def cosign(cart: dict, merchant: str) -> dict:
    """Produce the co-signed mandate (Build Guide §2)."""
    cart_id = cart.get("cart_id", "CART-1")
    mandate = {
        "cart_id": cart_id,
        "merchant": merchant,
        "items": cart.get("items", []),
        "merchant_signature": sign_cart(cart, merchant),
        "buyer_authorization": authorize(cart_id),
        "status": "co-signed",
        "booking_ref": cart.get("booking_ref", "SG-2026-0627-77"),
    }
    # AP2 integrity: bind the signed cart to a SHA-256 digest of its items so any
    # post-signing edit is detectable by verify(). Additive only — none of the
    # seven existing keys above are renamed, removed, or changed.
    mandate["cart_digest"] = _cart_digest(mandate["items"])
    return mandate


def verify(mandate: dict) -> bool:
    """Detect a cart altered after signing (Build Guide §4, Phase 7).

    Returns True only when ALL hold; any failure -> False, never raises:
      1. status == "co-signed"
      2. cart_digest is present on the mandate
      3. a fresh SHA-256 over mandate["items"] still equals that cart_digest
    A mandate built before this change (no cart_digest) fails (2); an item edited
    after cosign() (e.g. amount 400 -> 401) fails (3). This is a provable
    capability only — it is NOT wired into the live booking/payment path.
    """
    if mandate.get("status") != "co-signed":
        return False
    stored = mandate.get("cart_digest")
    if not stored:
        return False
    return _cart_digest(mandate.get("items", [])) == stored
