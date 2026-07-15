# AgentGate

> **AgentGate is an agent-reliability gate, not a security/trust gate.** Its honest v1 claim: it catches an **honest-but-fallible agent's mistakes** — arithmetic slips, decimal errors, LLM misreads, duplicates — at the moment of the write, and returns a machine-readable reason the agent can fix against. It does **NOT** defend against an adversarial agent that forges its own evidence, and it does **NOT** detect fraud or bad business judgment. Passing AgentGate means "the action is consistent with the evidence provided," never "the payment is correct or authorized."

A pre-action reliability gate for AI agents. When an agent proposes an action that
touches real systems ("approve invoice INV-001 for $12,500"), AgentGate checks it
against the caller-supplied source evidence and typed policies and returns
**ALLOW / BLOCK / ESCALATE**. Blocked actions come back with a compiler-grade,
machine-readable reason so the agent can correct itself.

See `DECISIONS.md` for the design reasoning. This project is a work in progress,
built incrementally.

## Status

The verification core is working end to end:

- **Grounding** — extract an invoice total from raw text and confirm the amount actually appears there, matched by numeric value, not substring (`grounded` / `not_grounded` / `ungroundable`).
- **Deterministic checks + decision** — structural arithmetic, currency, amount-vs-total, vendor, and duplicate checks over a structured invoice, producing a real **ALLOW / BLOCK / ESCALATE** decision with a machine-readable reason and a score.
- **Retry loop** — a caller consumes a BLOCK reason, applies the fix, and resubmits until it reaches ALLOW, or routes to a human when it can't. The decision record is never rewritten by the loop.
- **Honest evaluation** — a labeled dataset (`backend/eval/dataset.jsonl`) scored as precision, recall, and false-positive rate, not "accuracy." Escalating a legitimate payment counts as a false positive (the human cost of failing closed), and the consistent-but-wrong cases the threat model excludes are reported by name as known misses. Run it with `python -m eval.harness` from `backend/`. Current numbers: precision 0.64, recall 0.70, false-positive rate 0.44, in-scope recall 1.00 — imperfect on purpose; see `DECISIONS.md` (D6, D26).
- **Typed policy** — a YAML policy (`backend/policies/default.yaml`) adds escalation thresholds (amount ceiling, grounding-coverage score floor) within the fixed precedence; it can add escalations but never open the gate. When raw invoice text is supplied, the score reflects real grounding coverage of the fields the checks consume, and a total that does not appear in the source escalates decisively regardless of that score.

Still to come: an LLM agent with human-in-the-loop, a web dashboard, and an HTTP/MCP interface.

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e "backend[dev]"
cd backend && pytest        # live-provider tests are excluded by default
```
