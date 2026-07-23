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

const MONTHS: Record<string, string> = {
  january: "01", february: "02", march: "03", april: "04",
  may: "05", june: "06", july: "07", august: "08",
  september: "09", october: "10", november: "11", december: "12",
};

/** Convert "July 14, 2026" to "2026-07-14"; throws on unknown month. */
function isoDateFromLong(raw: string): string {
  const m = raw.match(/^([A-Za-z]+) (\d{1,2}), (\d{4})$/);
  const month = m && MONTHS[m[1].toLowerCase()];
  if (!m || !month) throw new Error(`Could not parse issue date: ${raw}`);
  return `${m[3]}-${month}-${m[2].padStart(2, "0")}`;
}

/**
 * Parse realistic plain-text invoices. Deterministic — no LLM — mirrors what
 * an upstream extractor would produce before AgentGate verifies. Two layouts
 * are recognized: the classic table format shipped in public/invoices/
 * (Invoice #: / Total Due:) and the Stripe-style billing layout
 * (Invoice number / Date of issue / Amount due, table with a Tax column).
 */
export function parseInvoiceText(raw_text: string): ParsedInvoice {
  // PDF text layers and viewer copies can carry invisible control characters
  // (e.g. a NUL where a hyphen glyph was); strip everything but \t and \n.
  const text = raw_text
    .replace(/\r\n/g, "\n")
    .replace(/[\u0000-\u0008\u000B-\u001F\u007F]/g, "")
    .trim();
  if (!text) throw new Error("Invoice text is empty.");
  if (
    /(?:Invoice|Quote) number/i.test(text) &&
    /Date of issue|Quote date|Amount due/i.test(text)
  ) {
    return parseStripeStyle(text);
  }
  return parseClassic(text);
}

/** Stripe-style billing layout (the format of most SaaS invoices). */
function parseStripeStyle(text: string): ParsedInvoice {
  const numberMatch = text.match(/(?:Invoice|Quote) number[ \t]+(.+)/i);
  if (!numberMatch) throw new Error("Could not find the document number (Invoice number / Quote number …).");
  // PDF text layers sometimes drop the hyphen glyph in "XXXX-0000" numbers,
  // leaving a gap (often a non-breaking space); rejoin the parts with the hyphen.
  const invoice_number = numberMatch[1].trim().replace(/\s+/g, "-");

  const dateMatch = text.match(
    /(?:Date of issue|Quote date)[ \t]+([A-Za-z]+ \d{1,2}, \d{4})/i,
  );
  if (!dateMatch) throw new Error("Could not find issue date (Date of issue / Quote date …).");
  const date = isoDateFromLong(dateMatch[1]);

  // Coordinate-reconstructed text keeps the two-column header, so the issuer
  // sits left of "Bill to" on one line. Viewer-copied text flattens columns
  // ("Bill to" stands alone) — fall back to the first company-looking line of
  // the seller block above it (skipping invoice metadata and registration ids).
  const vendorMatch = text.match(/^(.+?)[ \t]{2,}Bill to\b/im);
  let vendor = vendorMatch ? vendorMatch[1].trim() : "";
  if (!vendor) {
    const flatLines = text.split("\n").map((l) => l.trim());
    const billToIdx = flatLines.findIndex((l) => /^Bill to\b/i.test(l));
    for (let i = 0; i < billToIdx; i++) {
      const l = flatLines[i];
      if (!l) continue;
      if (/^(page \d|invoice\b|quote\b|date of issue|date due|expiration\b|vat\b)/i.test(l)) continue;
      if (/^[A-Z0-9]{6,}$/.test(l)) continue;
      vendor = l;
      break;
    }
  }
  if (!vendor) throw new Error("Could not find the issuing vendor (line before 'Bill to').");

  const currencyMatch = text.match(/\$[\d,]+\.\d{2}[ \t]+([A-Z]{3})\b/);
  const currency = currencyMatch ? currencyMatch[1] : "USD";

  const line_items: LineItem[] = [];
  let inTable = false;
  for (const line of text.split("\n")) {
    if (/^Description\b/i.test(line.trim())) {
      inTable = true;
      continue;
    }
    if (!inTable) continue;
    if (/^\s*Subtotal\b/i.test(line)) break;
    // description  qty  unit-price  tax%  amount — single-space separators
    // allowed (viewer copies collapse columns); the money/percent tail keeps
    // the match unambiguous.
    const row = line.match(
      /^[ \t]*(.+?)[ \t]+(\d+)[ \t]+\$?([\d,]+\.\d{2})[ \t]+[\d.]+%[ \t]+\$?([\d,]+\.\d{2})\s*$/,
    );
    if (!row) continue;
    const [, description, quantity, unitRaw, amountRaw] = row;
    line_items.push({
      description: description.trim(),
      quantity,
      unit_price: { value: normalizeMoney(unitRaw), currency },
      amount: { value: normalizeMoney(amountRaw), currency },
      kind: "charge",
    });
  }
  if (line_items.length === 0) {
    throw new Error("No line items found in invoice table.");
  }

  const totalMatch =
    text.match(/Amount due[ \t]+\$?([\d,]+\.\d{2})/i) ??
    text.match(/^\s*Total[ \t]+\$?([\d,]+\.\d{2})/im);
  if (!totalMatch) throw new Error("Could not find Amount due / Total.");

  const subtotalMatch = text.match(/Subtotal[ \t]+\$?([\d,]+\.\d{2})/i);

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
    total: { value: normalizeMoney(totalMatch[1]), currency },
    raw_text: text,
  };
}

/** Classic fixed-width table format (the fixtures in public/invoices/). */
function parseClassic(text: string): ParsedInvoice {
  const lines = text.split("\n");
  const vendor = titleCaseVendor(lines[0] ?? "Unknown Vendor");

  const invoiceMatch = text.match(/(?:Invoice|Quotation|Quote|Estimate)\s*#:\s*(\S+)/i);
  if (!invoiceMatch) throw new Error("Could not find the document number (Invoice #: / Quote #: …).");
  const invoice_number = invoiceMatch[1];

  const dateMatch = text.match(/^\s*(?:Issue|Quote|Estimate)? ?Date:\s*(\d{4}-\d{2}-\d{2})/im);
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

  // ^\s* anchoring keeps "Subtotal:" from matching the bare "Total:" form.
  const totalMatch =
    text.match(/(?:Total Due|Grand Total):\s*\$?([\d,]+\.\d{2})/i) ??
    text.match(/^\s*Total:\s*\$?([\d,]+\.\d{2})/im);
  if (!totalMatch) throw new Error("Could not find Total Due / Total.");
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
