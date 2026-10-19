"""
Payments scaffold for HorizonView — TEST MODE ONLY.

Status: the sandbox round is under construction. NOTHING here moves
real money: secret keys must start with sk_test_ (Paystack test keys),
and a live key is refused outright until the business is verified and
the operator explicitly flips the mode.

Decided rules from PAYMENTS_DECISION.md, enforced in code:

- the customer pays on PAYSTACK's hosted page; this app never sees
  card numbers or MoMo PINs, only a reference and a status
- a "payment succeeded" claim from the client is never a fact:
  status changes only after ServerSide verification
  (provider's verify response or a webhook with a valid signature)
- an amount mismatch (charged less than attempted) becomes
  'mismatch', which never unlocks anything
"""

import hashlib
import hmac
import os
from datetime import datetime, timezone

import httpx

PAYSTACK_BASE = "https://api.paystack.co"

ATTEMPTED = "attempted"
PAID = "paid"
MISMATCH = "mismatch"


class PaymentsNotConfigured(Exception):
    """Raised when the payment routes are used without keys — always
    a clear, actionable error, never a silent fallback."""


def secret_key() -> str:
    """The configured Paystack SECRET key. Refuses anything that is
    not a test key: scaffolding stays test-mode by construction."""
    raw = os.environ.get("PSTK_SECRET_TEST_KEY", "").strip()
    if not raw:
        raise PaymentsNotConfigured(
            "Payments are not configured yet. Create the Paystack TEST "
            "account (dashboard.paystack.com), copy your sk_test_... "
            "secret key into PSTK_SECRET_TEST_KEY in .env, and restart "
            "the server.")
    if not raw.startswith("sk_test_"):
        # User typed something else: wrong key or a live key
        raise PaymentsNotConfigured(
            "PSTK_SECRET_TEST_KEY must hold a Paystack TEST secret key "
            "(it starts with sk_test_). Live keys are not accepted "
            "here.")
    return raw


def new_reference() -> str:
    """Our internal, unique reference. Traceable, not guessable by
    sequence: a UUID segment is enough for symmetry with Paystack's
    own transaction id."""
    import uuid
    return f"HV-{uuid.uuid4().hex}"


def initiate_payment(secret: str, amount_ghs: float,
                     reference: str) -> tuple[str, str]:
    """Ask Paystack for a hosted checkout. Returns
    (provider_authorization_url, provider_payment_ref_or_our_reference).
    Uses the provider's TEST endpoint by construction of the key."""
    response = httpx.post(
        f"{PAYSTACK_BASE}/transaction/initialize",
        headers={"Authorization": f"Bearer {secret}"},
        json={
            "amount": int(round(amount_ghs * 100)),  # pesewas
            "currency": "GHS",
            "reference": reference,
        },
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("status"):
        raise RuntimeError(f"Paystack refused the initiation: "
                           f"{body.get('message', 'unknown reason')}")
    return body["data"]["authorization_url"], body["data"]["reference"]


def verify_payment(secret: str, reference: str) -> dict:
    """Server-side confirmation — the ONLY thing that can move an
    attempt to 'paid'. Returns the provider's charge data."""
    response = httpx.get(
        f"{PAYSTACK_BASE}/transaction/verify/{reference}",
        headers={"Authorization": f"Bearer {secret}"},
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("status"):
        raise RuntimeError("Paystack could not verify this reference: "
                           f"{body.get('message', 'unknown reason')}")
    return body["data"]


def webhook_signature_matches(raw_body: bytes, signature: str,
                              secret: str) -> bool:
    """Paystack signs webhook posts with HMAC-SHA512 of the exact raw
    body using the secret key. Compare in constant time."""
    if not signature:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def evaluate_charge(attempt: dict, charge: dict) -> str:
    """Turn a provider charge record into our status. succeeded=True
    only counts when the provider confirmed the SAME amount we
    attempted (in pesewas). Otherwise we record the honest truth:
    a mismatch, which never unlocks anything."""
    status = str(charge.get("status", "")).lower()
    provider_amount = int(charge.get("amount", 0))
    expected = int(round(float(attempt["amount_ghs"]) * 100))
    if status == "success" and provider_amount == expected:
        return PAID
    if status == "success":
        return MISMATCH  # 'paid', but NOT the agreed amount
    return ATTEMPTED  # anything else stays pending for verification


def display_attempt(a: dict) -> dict:
    """One attempt, ready for the owner's payment history. Money shows
    in Ghana cedis; the status is plain English."""
    labels = {ATTEMPTED: "Awaiting payment",
              PAID: "Paid (verified)"
              , MISMATCH: "Amount didn't match — under review"}
    return {
        "id": a["id"],
        "reference": a["provider_reference"],
        "amount_display": f"GH₵{float(a['amount_ghs']):,.2f}",
        "status": a["status"],
        "status_display": labels.get(a["status"], a["status"]),
        "test_mode": bool(a["test_mode"]),
        "created_at": a["created_at"],
        "verified_at": a["verified_at"],
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
