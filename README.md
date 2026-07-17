# AgentGate

> **AgentGate is an agent-reliability gate, not a security/trust gate.** Its honest v1 claim: it catches an **honest-but-fallible agent's mistakes** — arithmetic slips, decimal errors, LLM misreads, duplicates — at the moment of the write, and returns a machine-readable reason the agent can fix against. It does **NOT** defend against an adversarial agent that forges its own evidence, and it does **NOT** detect fraud or bad business judgment. Passing AgentGate means "the action is consistent with the evidence provided," never "the payment is correct or authorized."

A pre-action reliability gate for AI agents. When an agent proposes an action that
touches real systems ("approve invoice INV-001 for $12,500"), AgentGate checks it
against the caller-supplied source evidence and typed policies and returns
**ALLOW / BLOCK / ESCALATE**. Blocked actions come back with a compiler-grade,
machine-readable reason so the agent can correct itself.

See `DECISIONS.md` for the design reasoning. This project is a work in progress,
built incrementally.

**Live demo:** [dashboard](https://agentgate-two-pi.vercel.app) backed by the
[API](https://agentgate-api-mvob.onrender.com/health) (free tier — the first
request after idle can take up to a minute while the backend wakes).

![The dashboard blocking a misread amount, then allowing the corrected one](docs/demo.gif)

## Status

The verification core is working end to end:

- **Grounding** — extract an invoice total from raw text and confirm the amount actually appears there, matched by numeric value, not substring (`grounded` / `not_grounded` / `ungroundable`).
- **Deterministic checks + decision** — structural arithmetic, currency, amount-vs-total, vendor, and duplicate checks over a structured invoice, producing a real **ALLOW / BLOCK / ESCALATE** decision with a machine-readable reason and a score.
- **Frame stage** — before those content checks run, the gate confirms the action is even the thing they verify: an `approve_payment` (not a flag or a reject) against the invoice number it actually names. A wrong action type or a mismatched invoice number escalates to a human — it is never auto-"fixed," because the two readings of a mismatch (wrong evidence attached vs a typo'd action) have opposite corrections and the gate cannot tell which.
- **Retry loop** — a caller consumes a BLOCK reason, applies the fix, and resubmits until it reaches ALLOW, or routes to a human when it can't. The decision record is never rewritten by the loop.
- **Honest evaluation** — a labeled dataset (`backend/eval/dataset.jsonl`) scored as precision, recall, and false-positive rate, not "accuracy." Escalating a legitimate payment counts as a false positive (the human cost of failing closed), and the consistent-but-wrong cases the threat model excludes are reported by name as known misses. Run it with `python -m eval.harness` from `backend/`. Current numbers: precision 0.62, recall 0.73, false-positive rate 0.50, in-scope recall 1.00 — imperfect on purpose; see `DECISIONS.md` (D6, D26).
- **Typed policy** — a YAML policy (`backend/policies/default.yaml`) adds escalation thresholds (amount ceiling, grounding-coverage score floor) within the fixed precedence; it can add escalations but never open the gate. When raw invoice text is supplied, the score reflects real grounding coverage of the fields the checks consume, and a total that does not appear in the source escalates decisively regardless of that score.
- **Agent + human-in-the-loop** — a LangGraph agent (`backend/app/agent/graph.py`) proposes a payment, and on a block re-proposes using the machine-readable reason as feedback (capped by the policy); on an escalate it pauses for a human to approve or reject. The correction is value-only — the agent can only change the field the gate flagged, never smuggle in an adjustment — and a malformed model response fails closed to a human rather than crashing. A human approval routes the action but never rewrites the recorded decision.
- **Fail-closed input boundary** — input that cannot be parsed becomes a valid `escalate` decision with a `null` score via a pure factory (never a crash, never an allow), with error messages bounded so raw caller text never rides into traces or the dashboard. Every caller-supplied text field is length-capped at the schema; anything over a cap is rejected and escalates to a human. This is the contract the HTTP API sits on.
- **HTTP API** — `POST /verify` serves the gate and always answers **HTTP 200 with a decision** (verified or fail-closed): an undecodable body, a schema-invalid field, an oversized request (1 MiB cap), or an unexpected internal error all become a valid `escalate` decision — never a 5xx, never a framework 422, never an allow. Unknown fields anywhere in the request are rejected rather than silently dropped (a misspelled `adjustments` key must not turn a declared withholding into an auto-"fixed" full payment), money may be sent as JSON strings or numbers (decoded to exact decimals, floats never exist), and every decimal comes back as a JSON string so nothing re-floats it downstream. `/verify` is read-only — it reads the duplicate store, records nothing, so a dry run never burns an invoice number. Optional Langfuse tracing observes decisions without being able to affect them (no keys → no-op; of raw invoice text it records length only, never content).

- **Web dashboard** — a Next.js dashboard (`frontend/`) that submits a request body to the gate **verbatim** and renders the decision exactly as returned: the banner, the machine-readable reasons, the checks table, score (`null` renders as "not computed", never 0), evidence, and trace id. Money stays the exact string the gate returned — the UI does no float math, and pasting garbage into the request box demonstrates the fail-closed contract live. Covered by a Playwright end-to-end suite that boots the real backend and crosses real CORS, in CI on every push.
- **MCP server + pip package** — `pip install .` installs `agentgate` with the default policy shipped inside the wheel, and `agentgate-mcp` serves a `verify_action` tool over stdio for MCP-speaking agents. The tool runs the same validation and decision path as the HTTP API and always returns a Decision — never a tool error the calling agent might route around. Money over MCP must be JSON strings: the transport parses JSON before AgentGate sees it, so a numeric amount is already a lossy float and is rejected into a fail-closed escalate. A CI job installs the package into a clean environment on every push and verifies a decision from it.

Next milestone (post-v1): **independent source fetch** — pulling the invoice from a system of record by an identifier the agent provides, so the agent chooses *which* invoice but never *what it says*. That is the upgrade that turns consistency-checking into a real trust anchor; see the threat model above.

## Quickstart

Ask the live gate to verify an action (money values are strings — exactness is the product):

```bash
curl -s -X POST https://agentgate-api-mvob.onrender.com/verify \
  -H 'Content-Type: application/json' \
  -d '{
    "proposed_action": {"action_type": "approve_payment", "invoice_number": "INV-001",
                        "amount": {"value": "12400.00", "currency": "USD"}, "vendor": "Acme Corp"},
    "source": {"invoice": {"invoice_number": "INV-001", "vendor": "Acme Corp",
               "date": "2026-01-15", "currency": "USD",
               "line_items": [{"description": "Widget", "quantity": "1",
                 "unit_price": {"value": "1240.00", "currency": "USD"},
                 "amount": {"value": "1240.00", "currency": "USD"}, "kind": "charge"}],
               "total": {"value": "1240.00", "currency": "USD"}}}}'
```

The response is a `block` with `"field_to_change": "proposed_action.amount"` and the exact expected value — a reason an agent can fix against and resubmit. Or use it as a library:

```bash
pip install "git+https://github.com/varunk14/AgentGate.git#subdirectory=backend"
```

```python
from agentgate.core.decision import decide
from agentgate.core.schemas import Invoice, ProposedAction

decision = decide(Invoice.model_validate(invoice_dict),
                  ProposedAction.model_validate(action_dict))
print(decision.decision, [r.message for r in decision.reasons])
```

Or as an MCP server for an agent runtime (install with the `mcp` extra, then register the stdio command `agentgate-mcp`; the tool is `verify_action`).

## Architecture

```
proposed action  +  caller-supplied evidence (structured invoice, optional raw text)
        |
        v
  frame stage          is this even an approve_payment against the invoice it
        |              names?  wrong frame -> ESCALATE, score null
        v
  deterministic        structural arithmetic, currency, amount-vs-total,
  checks               vendor, duplicate — pure functions, Decimal only
        |
        v
  grounding            do the invoice's numbers literally appear in the raw
        |              text?  token-level Decimal match, never substring
        v
  policy               amount ceiling, score floor — config can add
        |              escalations, never open the gate
        v
  ALLOW   |   BLOCK (machine-fixable reason)   |   ESCALATE (human)
```

The only LLM in the system sits behind `llm_router.py` for extraction; the
decision path is pure and deterministic, and anything the gate cannot verify
fails closed to a human. In tests, the router is the only mocked seam.

## Evaluation, honestly

Scored as interventions vs. hand-labeled ground truth over 21 cases —
escalating a legitimate payment counts as a **false positive** (the human cost
of failing closed), and the misses the threat model predicts are reported by
name, not hidden. Run it: `python -m eval.harness` from `backend/`.

| Metric | Value |
| --- | --- |
| Precision | 0.615 |
| Recall | 0.727 |
| False-positive rate | 0.500 |
| In-scope recall | 1.000 |

Known misses (out of scope by design — consistent-but-wrong sources that only
the independent-fetch milestone can catch): `oos_doctored_source`,
`oos_renumbered_double_bill`, `oos_unauthorized_spend`. The false positives
are fail-closed escalations of legitimate-but-unverifiable payments (a
declared withholding, a vendor rename, a total-only invoice, an over-threshold
amount, a reject the gate cannot judge).

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e "backend[dev]"
cd backend && pytest        # live-provider tests are excluded by default
```

Run the API locally (from `backend/`, with the `server` extra installed):

```bash
pip install -e ".[server]"
uvicorn agentgate.main:app                 # GET /health, POST /verify
```

Optional environment: `AGENTGATE_DB_PATH` points the duplicate store at a file
(default is in-memory); `AGENTGATE_CORS_ORIGINS` grants cross-origin access to
exact browser origins (unset = none); `LANGFUSE_PUBLIC_KEY` /
`LANGFUSE_SECRET_KEY` (and optionally `LANGFUSE_HOST`) enable tracing — see
`backend/.env.example`.

Run the dashboard (from `frontend/`):

```bash
npm install
npm run dev                          # expects the API on http://127.0.0.1:8000
npm run e2e                          # Playwright gate: boots backend + frontend itself
```

Point it at a different API with `NEXT_PUBLIC_AGENTGATE_API`; set
`NEXT_PUBLIC_TRACE_URL_TEMPLATE` (a URL containing `{id}`) to turn trace ids
into links.

## Deploy

The backend deploys to Render from `render.yaml` (free plan; ~1 minute cold
start after idle, ephemeral disk). The frontend deploys to Vercel from
`frontend/`. Order matters: bring up the backend, build the frontend with
`NEXT_PUBLIC_AGENTGATE_API` set to the backend URL, then set that Vercel
origin in the backend's `AGENTGATE_CORS_ORIGINS`.

## License

MIT — see `LICENSE`.
