// Wire types mirroring the AgentGate Decision contract (PRD section 6).
// Every Decimal arrives as a JSON STRING ("1240.00", score "0.97") and must be
// displayed verbatim — parseFloat would reintroduce the float the backend
// exists to exclude (D1/D35). score is null when NOT computed (D32).

export interface Money {
  value: string;
  currency: string;
}

export interface Check {
  name: string;
  type: "critical" | "soft";
  passed: boolean;
  detail: string;
}

export interface BlockReason {
  check: string;
  expected: Money | string | null;
  received: Money | string | null;
  field_to_change: string | null;
  block_type: "agent_fixable" | "source_invalid" | null;
  message: string;
}

export interface Decision {
  decision: "allow" | "block" | "escalate";
  score: string | null;
  checks: Check[];
  reasons: BlockReason[];
  evidence_used: string[];
  proposed_action: unknown;
  trace_id: string | null;
  latency_ms: number | null;
  timestamp: string | null;
}
