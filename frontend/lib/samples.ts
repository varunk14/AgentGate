// Sample request bodies, value-identical to the backend's spec-pinned test
// cases so what the buttons demonstrate is exactly what the test gates pin.
// The UI serializes these once into the request box; from there the raw text
// is what gets sent (D39 — the gate is the validator, the UI is a pipe).

const invoice = {
  invoice_number: "INV-001",
  vendor: "Acme Corp",
  date: "2026-01-15",
  currency: "USD",
  line_items: [
    {
      description: "Widget",
      quantity: "1",
      unit_price: { value: "1240.00", currency: "USD" },
      amount: { value: "1240.00", currency: "USD" },
      kind: "charge",
    },
  ],
  tax_lines: [],
  total: { value: "1240.00", currency: "USD" },
};

const action = {
  action_type: "approve_payment",
  invoice_number: "INV-001",
  amount: { value: "1240.00", currency: "USD" },
  vendor: "Acme Corp",
  adjustments: [],
  agent_rationale: "Totals match.",
};

export interface Sample {
  id: string;
  label: string;
  expects: string;
  body: unknown;
}

export const SAMPLES: Sample[] = [
  {
    id: "clean",
    label: "Clean invoice",
    expects: "allow",
    body: { proposed_action: action, source: { invoice } },
  },
  {
    id: "tampered",
    label: "Misread amount",
    expects: "block (agent-fixable)",
    body: {
      proposed_action: { ...action, amount: { value: "12400.00", currency: "USD" } },
      source: { invoice },
    },
  },
  {
    id: "reject",
    label: "Reject action (out of frame)",
    expects: "escalate, score not computed",
    body: {
      proposed_action: { ...action, action_type: "reject" },
      source: { invoice },
    },
  },
  {
    id: "ungrounded",
    label: "Total missing from raw text",
    expects: "escalate (total not grounded)",
    body: {
      proposed_action: action,
      source: {
        invoice,
        raw_text: "Totally different document. Total Due: $999.99",
      },
    },
  },
];
