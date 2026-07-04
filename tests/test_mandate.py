"""Unit tests for the AP2 tampered-cart mandate catch (payments/mandate.py).

Stdlib unittest only — no pytest, no third-party deps. Run from the repo root:
    python -m unittest tests/test_mandate.py
"""
from unittest import TestCase, main

from payments.mandate import cosign, verify


def _sample_cart():
    """A cart with one line item carrying a numeric amount."""
    return {
        "cart_id": "CART-TEST",
        "items": [{"id": "li_1", "desc": "KBBQ set", "amount": 400, "quantity": 1}],
        "booking_ref": "SG-TEST-01",
    }


class TamperCatchTests(TestCase):
    def test_a_clean_mandate_verifies(self):
        """cosign a cart with items -> verify() is True and cart_digest exists."""
        mandate = cosign(_sample_cart(), "Seoul Garden")
        self.assertIn("cart_digest", mandate)
        self.assertTrue(verify(mandate))

    def test_b_altered_amount_fails(self):
        """Edit items[0]['amount'] AFTER signing -> verify() is False."""
        mandate = cosign(_sample_cart(), "Seoul Garden")
        mandate["items"][0]["amount"] += 1  # 400 -> 401
        self.assertFalse(verify(mandate))

    def test_c_status_not_cosigned_fails(self):
        """Flip status away from 'co-signed' -> verify() is False."""
        mandate = cosign(_sample_cart(), "Seoul Garden")
        mandate["status"] = "pending"
        self.assertFalse(verify(mandate))

    def test_d_missing_cart_digest_fails(self):
        """A pre-change mandate (co-signed, but no cart_digest) -> verify() False."""
        mandate = {
            "cart_id": "CART-TEST",
            "merchant": "Seoul Garden",
            "items": [{"id": "li_1", "amount": 400}],
            "merchant_signature": "0xVENUE",
            "buyer_authorization": "0xCONVENER",
            "status": "co-signed",
            "booking_ref": "SG-TEST-01",
        }
        self.assertNotIn("cart_digest", mandate)
        self.assertFalse(verify(mandate))


if __name__ == "__main__":
    main()
