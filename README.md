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

Still to come: policy config, an LLM agent with human-in-the-loop, a web dashboard, and an HTTP/MCP interface.

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e "backend[dev]"
cd backend && pytest        # live-provider tests are excluded by default
```
