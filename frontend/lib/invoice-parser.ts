/** Structured invoice shape sent to POST /verify (money as strings, D1). */

export interface Money {
  value: string;
  currency: string;
}

export interface LineItem {
  description: string;
  quantity: string;
  unit_price: Money;
  amount: Money;
  kind: "charge" | "shipping" | "discount";
}

export interface ParsedInvoice {
  invoice_number: string;
  vendor: string;
  date: string;
  currency: string;
  line_items: LineItem[];
  tax_lines: { rate: string; amount: Money }[];
  subtotal?: Money;
  total: Money;
  raw_text: string;
}

function normalizeMoney(raw: string): string {
  const cleaned = raw.replace(/,/g, "").replace(/^\$/, "").trim();
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) {
    throw new Error(`Could not parse money value: ${raw}`);
  }
  const [whole, frac = ""] = cleaned.split(".");
  return `${whole}.${(frac + "00").slice(0, 2)}`;
}

function titleCaseVendor(line: string): string {
  return line
    .trim()
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Parse realistic plain-text invoices (Acme / Northwind demo formats shipped
 * in public/invoices/). Deterministic — no LLM — mirrors what an upstream
 * extractor would produce before AgentGate verifies.
 */
export function parseInvoiceText(raw_text: string): ParsedInvoice {
  const text = raw_text.replace(/\r\n/g, "\n").trim();
  if (!text) throw new Error("Invoice text is empty.");

  const lines = text.split("\n");
  const vendor = titleCaseVendor(lines[0] ?? "Unknown Vendor");

  const invoiceMatch = text.match(/Invoice #:\s*(\S+)/i);
  if (!invoiceMatch) throw new Error("Could not find invoice number (Invoice #: …).");
  const invoice_number = invoiceMatch[1];

  const dateMatch = text.match(/Issue Date:\s*(\d{4}-\d{2}-\d{2})/i);
  if (!dateMatch) throw new Error("Could not find issue date.");
  const date = dateMatch[1];

  const currency = "USD";
  const line_items: LineItem[] = [];
  let inTable = false;

  for (const line of lines) {
    if (/^Description/i.test(line.trim())) {
      inTable = true;
      continue;
    }
    if (!inTable) continue;
    if (/Subtotal:/i.test(line)) break;
    if (/^-{4,}/.test(line) || !line.trim()) continue;

    const row = line.match(
      /^[ \t]*(.+?)[ \t]{2,}(-|\d+)[ \t]+(-|\$?[\d,]+\.\d{2})[ \t]+\$?([\d,]+\.\d{2})\s*$/,
    );
    if (!row) continue;

    const [, description, qtyRaw, unitRaw, amountRaw] = row;
    const amountValue = normalizeMoney(amountRaw);
    const quantity = qtyRaw === "-" ? "1" : qtyRaw;
    const unitValue =
      unitRaw === "-" ? amountValue : normalizeMoney(unitRaw.replace(/^\$/, ""));

    const kind: LineItem["kind"] = /shipping/i.test(description)
      ? "shipping"
      : "charge";

    line_items.push({
      description: description.trim(),
      quantity,
      unit_price: { value: unitValue, currency },
      amount: { value: amountValue, currency },
      kind,
    });
  }

  if (line_items.length === 0) {
    throw new Error("No line items found in invoice table.");
  }

  const totalMatch = text.match(/Total Due:\s*\$?([\d,]+\.\d{2})/i);
  if (!totalMatch) throw new Error("Could not find Total Due.");
  const total = { value: normalizeMoney(totalMatch[1]), currency };

  const subtotalMatch = text.match(/Subtotal:\s*\$?([\d,]+\.\d{2})/i);

  return {
    invoice_number,
    vendor,
    date,
    currency,
    line_items,
    tax_lines: [],
    subtotal: subtotalMatch
      ? { value: normalizeMoney(subtotalMatch[1]), currency }
      : undefined,
    total,
    raw_text: text,
  };
}

export interface AgentProposal {
  action_type: "approve_payment" | "reject";
  amount_value: string;
  vendor: string;
  agent_rationale: string;
}

const UNRELATED_SOURCE_TEXT =
  "Totally different document. Total Due: $999.99";

export function buildCallerVerifyRequest(
  parsed: ParsedInvoice,
  proposal: AgentProposal,
  options: {
    include_raw_text: boolean;
    /** Demo-only: prove grounding failure with real invoice structure. */
    force_bad_grounding?: boolean;
  },
): { proposed_action: unknown; source: unknown } {
  const { invoice_number, vendor, date, currency, line_items, tax_lines, subtotal, total } =
    parsed;

  const invoice: Record<string, unknown> = {
    invoice_number,
    vendor,
    date,
    currency,
    line_items,
    tax_lines,
    total,
  };
  if (subtotal) invoice.subtotal = subtotal;

  const source: Record<string, unknown> = { invoice };
  if (options.force_bad_grounding) {
    source.raw_text = UNRELATED_SOURCE_TEXT;
  } else if (options.include_raw_text) {
    source.raw_text = parsed.raw_text;
  }

  return {
    proposed_action: {
      action_type: proposal.action_type,
      invoice_number,
      amount: { value: proposal.amount_value, currency },
      vendor: proposal.vendor,
      adjustments: [],
      agent_rationale: proposal.agent_rationale,
    },
    source,
  };
}

export function buildFetchVerifyRequest(
  fetchId: string,
  proposal: Omit<AgentProposal, "vendor"> & { vendor: string; invoice_number: string },
): { proposed_action: unknown; source: unknown } {
  return {
    proposed_action: {
      action_type: proposal.action_type,
      invoice_number: proposal.invoice_number,
      amount: { value: proposal.amount_value, currency: "USD" },
      vendor: proposal.vendor,
      adjustments: [],
      agent_rationale: proposal.agent_rationale,
    },
    source: { fetch: fetchId },
  };
}

/** Classic agent misread: $1,240.00 interpreted as $12,400.00 */
export function decimalSlipAmount(correctTotal: string): string {
  const normalized = normalizeMoney(correctTotal);
  const [whole, frac] = normalized.split(".");
  return `${whole}0.${frac}`;
}

export function defaultProposal(parsed: ParsedInvoice): AgentProposal {
  return {
    action_type: "approve_payment",
    amount_value: parsed.total.value,
    vendor: parsed.vendor,
    agent_rationale: "Payment matches invoice total and vendor.",
  };
}
