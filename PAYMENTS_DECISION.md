# Payments provider decision item

Status: **DECISION DOCUMENT — nothing is wired, nothing collects money.**
HorizonView does not collect any payments today. The dashboard's
"SERVICE LEVELS" panels are labelled `COMING LATER` for exactly this
reason. This document records what we decided BEFORE a single line of
payment code exists, so the rules can't get improvised badly later.

## Where payments would fit (the need, plainly)

A Ghanaian client picks a service level (or, later, funds a project
milestone) and pays electronically — most naturally **mobile money**
(MTN MoMo first, Telecel Cash, AirtelTigo Money), cards second.
The app then records: who paid, how much, against what, and their
transaction reference — and marks the service level active.

## Non-negotiable rules (decided before any provider is chosen)

1. **Sandbox (test mode) first.** Nothing real is charged until the
   operator's business is verified with the provider and live keys
   exist in `.env`.
2. **Test keys are secrets.** They live in `.env` (gitignored). Never
   in source code, never in templates, never sent to the browser.
3. **HorizonView never touches card numbers or MoMo PINs.** The
   customer enters payment details on the PROVIDER's hosted checkout
   page. We store only the reference and status. (This also keeps
   card-security obligations with the certified provider, not us.)
4. **Every payment is verified server-side** against the provider's
   verification endpoint before any service unlocks. Client-sent
   "I paid" messages are treated as claims, not facts.
5. **No payment UI or claims anywhere** until rules 1–4 have passed.
6. **No invented numbers.** Fees and settlement times are recorded
   from the provider's own published pages at sign-up — third-party
   blog figures are starting points, never our records.

## The candidates (researched 2026-10-19)

| Candidate | Ghana MoMo + GHS | Verified sandbox | Local in Ghana | Notes |
|---|---|---|---|---|
| **Paystack** | ✓ MoMo supported for Ghana | ✓ self-serve test keys | Ghana office (Cantonments, Accra) | Stripe-acquired; developer docs consistently rated cleaner than peers; supports MoMo for Ghana; 300k+ merchants |
| **Flutterwave** | ✓ dedicated Ghana MoMo docs | ✓ self-serve sandbox | — | broader method coverage in Ghana incl. bank transfer via virtual accounts (launched Ghana bank transfer 2025); heavier docs historically |
| **Hubtel** | ✓ native (Ghana-built) | unclear — self-serve sandbox not evident from public site | ✓ HQ Ghana | strong MoMo/bank APIs, bill payments, 20-year local history; onboarding appears account-manager led |
| **MTN MoMo direct** | ✓ but MTN-only | developer portal sandbox ✓ | ✓ | needs registered business merchant account; one network per integration (Telecel/AirtelTigo separate) |
| **Local aggregators** (IT Consortium TransFlow, Nsano, ExpressPay) | ✓ | varies (mostly sales-led) | ✓ | deep MTN relationships; onboarding relationship-based, not self-serve |
| PawaPay | ✓ MoMo-only aggregator | ✓ sandbox | — | built for platforms/NGOs at volume scale; likely oversized for now |

One important shape of this decision: a provider's **sandbox** is a
self-serve signup (account + test keys in minutes) — a provider's
**going live** almost always includes paying out of a verified
business account; business registration details are typically needed
then. Confirm the exact requirement at sign-up, never assume.

## DECISION (what this memo decides)

**First sandbox round: Paystack.** Reasoning in plain order:

1. Cheapest road to a REAL test: self-serve account + instantly
   usable test keys, no sales conversation required.
2. Ghana MoMo is documented for Ghana currency (GHS) and supports
   the three networks, so a full test checkout is possible.
3. The developer-experience edge (cleaner API + webhooks documented
   and behaving predictably) matters most for a first integration.
4. Ghana presence (Accra office) exists for the later go-live step.

**Runner-up in waiting: Flutterwave.** If Paystack's Ghana fee
schedule or go-live requirements prove unfriendly, the second sandbox
round is Flutterwave — its Ghana bank-transfer virtual accounts are a
meaningful extra capability.

**Deliberately not chosen yet:** MTN MoMo direct (single-network,
business registration first — reversing the order); Hubtel and local
aggregators (no clear self-serve sandbox; revisit if after a few
sandbox rounds we want settlement-heavy features like instant MoMo
disbursements).

## The sandbox-first plan (only after this memo is approved)

1. Operator opens a Paystack account and copies the **TEST** keys.
2. Test keys go into `.env` (gitignored). A placeholder check is
   added so the app refuses to start payment code without them.
3. Build, behind an obvious test-mode banner, the smallest honest
   loop: create an attempt → provider test checkout → webhook +
   server-side verification → record reference/status in the
   database → dashboard shows payment history (test-labelled).
4. Tests with mocked provider responses only (existing testing rule
   applies: pytest runs offline, no real network).
5. Only after the sandbox round is proven and documented do we talk
   about business verification and live keys.

## What this memo does NOT promise

- Any fee number, settlement time, or guarantee of approval — the
  provider's own published pages at sign-up are the record.
- That the business registration requirement will suit the operator's
  situation — a go-live prerequisite to confirm at sign-up.
- widget-level proof of MoMo prompts in test mode for all three
  networks — that is step 3's outcome, not this memo's claim.
