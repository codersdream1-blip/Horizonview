"""
Payments scaffold tests — the sandbox round's rule file.

Paystack is NEVER contacted: its two integration points (checkout
initiation and the webhook) are stubbed or faked here, exactly as the
project's testing rule requires. The real network test comes later,
with the operator's own test keys.

Rules under permanent test:
- the ladder (anon 401 / non-owner 404 / CSRF 403) applies to money
  routes like everything else
- no keys configured -> a clear, actionable 503, never a fallback,
  never a fake checkout link (and by default the env is CLEAN, so
  this stays true even if the operator's .env has keys one day)
- a webhook without a valid body signature is a 401, every time
- only a verified, amount-matching charge becomes 'paid'
- a WRONG amount on a successful charge becomes 'mismatch', which
  never pretends to be a payment
- the test-mode label travels with every attempt
"""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

import db
import main
import payments

EMAIL = "ama@example.com"
OTHER = "yaw@example.com"
PASSWORD = "sandbox-pass-3"

FAKE_SECRET = "sk_test_" + "x" * 30


@pytest.fixture(autouse=True)
def clean_keys_env(monkeypatch):
    """By default NO keys are configured — regardless of what the
    operator's .env holds someday. Tests that want sandbox keys ask
    for the sandbox_keys fixture; tests of the unconfigured state ask
    for nothing."""
    monkeypatch.delenv("PSTK_SECRET_TEST_KEY", raising=False)


@pytest.fixture()
def sandbox_keys(monkeypatch):
    monkeypatch.setenv("PSTK_SECRET_TEST_KEY", FAKE_SECRET)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    return TestClient(main.app)


def register_and_login(client, email=EMAIL, name="Ama Mensah"):
    client.post("/api/register", json={
        "full_name": name, "email": email, "phone": "+233 20 600 0000",
        "password": PASSWORD,
    })
    reply = client.post("/api/login",
                        json={"email": email, "password": PASSWORD})
    assert reply.status_code == 200
    return {"X-CSRF-Token": client.cookies.get("csrf_token", "")}


def register_other(client):
    client.post("/api/register", json={
        "full_name": "Yaw Owusu", "email": OTHER,
        "phone": "+233 20 600 0001", "password": PASSWORD,
    })
    return client.post("/api/login",
                       json={"email": OTHER, "password": PASSWORD})


def make_project(client, headers=None):
    return client.post("/api/projects", json={
        "name": "Kasoa Fencing (SANDBOX)", "project_type": "construction",
        "location": "Kasoa", "budget": "45000",
        "contractor_name": "Prototype Data",
    }, headers=headers)


def sign(body: bytes, secret: str = FAKE_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()


def stub_initiate(monkeypatch):
    """The provider boundary stub: no network, ever."""
    seen = {}

    def fake_initiate(secret, amount_ghs, reference):
        seen["secret"] = secret
        seen["amount"] = amount_ghs
        seen["reference"] = reference
        return (
            "https://checkout.paystack.com/test-fake",
            f"PSTACK-{reference}")
    monkeypatch.setattr(payments, "initiate_payment", fake_initiate)
    return seen


def post_webhook(client, payload: dict, secret: str = FAKE_SECRET,
                 signature=None):
    """None -> sign correctly (the honest provider); "" -> unsigned."""
    raw = json.dumps(payload).encode()
    if signature is None:
        signature = sign(raw, secret)
    return client.post(
        "/api/payments/webhook/paystack",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "x-paystack-signature": signature,
        })


# ---------- configured-keys honesty ----------

def test_no_keys_means_a_clear_503_never_a_fake_link(client):
    headers = register_and_login(client)
    make_project(client, headers)

    reply = client.post("/api/projects/1/payments",
                        json={"amount_ghs": "500"}, headers=headers)
    assert reply.status_code == 503
    body = reply.json()
    assert "not configured" in body["message"]
    assert "sk_test_" in body["message"]  # actionable: what is needed
    assert "checkout_url" not in body


def test_a_live_key_is_refused_outright(client, monkeypatch):
    monkeypatch.setenv("PSTK_SECRET_TEST_KEY", "sk_live_" + "y" * 30)
    headers = register_and_login(client)
    make_project(client, headers)
    reply = client.post("/api/projects/1/payments",
                        json={"amount_ghs": "500"}, headers=headers)
    assert reply.status_code == 503
    assert "TEST secret key" in reply.json()["message"]


# ---------- the ladder ----------

def test_anonymous_gets_401_not_probing(client):
    reply = client.post("/api/projects/1/payments",
                        json={"amount_ghs": "500"})
    assert reply.status_code == 401
    reply = client.get("/api/projects/1/payments")
    assert reply.status_code == 401


def test_non_owner_gets_404_not_exposure(client):
    headers = register_and_login(client)
    make_project(client, headers)
    register_other(client)
    other_headers = {"X-CSRF-Token": client.cookies.get("csrf_token", "")}

    reply = client.post("/api/projects/1/payments",
                        json={"amount_ghs": "500"}, headers=other_headers)
    assert reply.status_code == 404
    reply = client.get("/api/projects/1/payments", headers=other_headers)
    assert reply.status_code == 404


def test_missing_csrf_gets_403(client, sandbox_keys, monkeypatch):
    stub_initiate(monkeypatch)
    headers = register_and_login(client)
    make_project(client, headers)
    reply = client.post("/api/projects/1/payments", json={"amount_ghs": "500"})
    assert reply.status_code == 403


# ---------- checkout creation ----------

def test_checkout_attempt_is_recorded_client_side_only(client, sandbox_keys,
                                                       monkeypatch):
    seen = stub_initiate(monkeypatch)
    headers = register_and_login(client)
    make_project(client, headers)

    reply = client.post("/api/projects/1/payments",
                        json={"amount_ghs": "4,500.50"}, headers=headers)

    assert reply.status_code == 201, reply.text
    body = reply.json()
    assert body["checkout_url"].startswith("https://")
    assert body["attempt"]["test_mode"] is True
    assert body["attempt"]["status"] == "attempted"
    assert body["attempt"]["amount_display"] == "GH₵4,500.50"
    # the authoritative amount recorded server-side travels to the
    # provider: comma parsing did its job there too
    assert seen["amount"] == 4500.50
    assert seen["secret"].startswith("sk_test_")
    # db row exists for later verification
    stored = db.get_payment_attempt_by_reference(body["reference"])
    assert stored is not None and stored["status"] == "attempted"


def test_bad_amounts_are_refused(client, sandbox_keys, monkeypatch):
    stub_initiate(monkeypatch)
    headers = register_and_login(client)
    make_project(client, headers)

    for bad in ("0", "-5", "banana", ""):
        reply = client.post("/api/projects/1/payments",
                            json={"amount_ghs": bad}, headers=headers)
        assert reply.status_code == 400, bad


def test_payment_history_is_owner_only_and_honest(client, sandbox_keys,
                                                  monkeypatch):
    stub_initiate(monkeypatch)
    headers = register_and_login(client)
    make_project(client, headers)
    client.post("/api/projects/1/payments",
                json={"amount_ghs": "1,000"}, headers=headers)

    listing = client.get("/api/projects/1/payments", headers=headers).json()
    assert len(listing["payments"]) == 1
    attempt = listing["payments"][0]
    assert attempt["status"] == "attempted"  # nothing paid yet, said so
    assert attempt["test_mode"] is True


# ---------- webhook: the only door to 'paid' ----------

def test_webhook_marks_matching_success_as_paid(client, sandbox_keys):
    db.create_payment_attempt(
        project_id=1, owner_id=1, provider="paystack",
        provider_reference="HV-TEST1", amount_ghs=500.0, test_mode=True)

    reply = post_webhook(client, {
        "event": "charge.success",
        "data": {"reference": "HV-TEST1", "amount": 50000,
                 "status": "success"},
    })

    assert reply.status_code == 200, reply.text
    assert reply.json()["status"] == "paid"
    refreshed = db.get_payment_attempt_by_reference("HV-TEST1")
    assert refreshed["status"] == "paid"
    assert refreshed["verified_at"] is not None


def test_webhook_rejects_bad_signature(client, sandbox_keys):
    db.create_payment_attempt(
        project_id=1, owner_id=1, provider="paystack",
        provider_reference="HV-TEST2", amount_ghs=500.0, test_mode=True)

    reply = post_webhook(client, {
        "event": "charge.success",
        "data": {"reference": "HV-TEST2", "amount": 50000,
                 "status": "success"},
    }, signature="deadbeef" * 16)

    assert reply.status_code == 401
    assert db.get_payment_attempt_by_reference(
        "HV-TEST2")["status"] == "attempted"  # untouched


def test_webhook_rejects_unsigned(client, sandbox_keys):
    reply = post_webhook(client, {"event": "charge.success",
                                  "data": {}}, signature="")
    assert reply.status_code == 401


def test_underpaid_success_never_becomes_paid(client, sandbox_keys):
    """Someone pays GH₵50 against a GH₵500 attempt: recorded as a
    MISMATCH, which never unlocks anything — the honest status."""
    db.create_payment_attempt(
        project_id=1, owner_id=1, provider="paystack",
        provider_reference="HV-TEST3", amount_ghs=500.0, test_mode=True)

    reply = post_webhook(client, {
        "event": "charge.success",
        "data": {"reference": "HV-TEST3", "amount": 5000,
                 "status": "success"},
    })

    assert reply.json()["status"] == payments.MISMATCH
    assert db.get_payment_attempt_by_reference(
        "HV-TEST3")["status"] == payments.MISMATCH


def test_webhook_reports_unknown_reference(client, sandbox_keys):
    reply = post_webhook(client, {
        "event": "charge.success",
        "data": {"reference": "HV-GHOST", "amount": 1000,
                 "status": "success"},
    })
    assert reply.status_code == 404


def test_other_events_are_acked_without_action(client, sandbox_keys):
    reply = post_webhook(client, {
        "event": "transfer.success",
        "data": {"reference": "HV-TEST4"},
    })
    assert reply.status_code == 200
    assert reply.json() == {"ok": True}


# ---------- pure amount gate ----------

def test_evaluate_charge_gates_the_amount():
    attempt = {"amount_ghs": 123.45}
    assert payments.evaluate_charge(
        attempt, {"status": "success", "amount": 12345}) == "paid"
    assert payments.evaluate_charge(
        attempt, {"status": "success", "amount": 12300}) == payments.MISMATCH
    assert payments.evaluate_charge(
        attempt, {"status": "failed", "amount": 12345}) == "attempted"


def test_display_attempt_is_plain_english():
    a = {"id": 1, "provider_reference": "HV-X", "amount_ghs": 1000.0,
         "status": "attempted", "test_mode": 1,
         "created_at": "2026-10-19T09:00:00+00:00", "verified_at": None}
    shown = payments.display_attempt(a)
    assert shown["status_display"] == "Awaiting payment"
    assert shown["test_mode"] is True
    a["status"] = payments.PAID
    assert payments.display_attempt(a)["status_display"] == \
        "Paid (verified)"


def test_webhook_signature_is_exact_hmac_sha512():
    body = b'{"event":"charge.success"}'
    correct = hmac.new(FAKE_SECRET.encode(), body, hashlib.sha512).hexdigest()
    assert payments.webhook_signature_matches(body, correct, FAKE_SECRET)
    assert not payments.webhook_signature_matches(
        body + b" ", correct, FAKE_SECRET)  # body tampered
    assert not payments.webhook_signature_matches(
        body, correct[:-1] + "0", FAKE_SECRET)  # signature tampered
