export type InvoiceSourceMode = "file" | "fetch";

export interface RealInvoiceFile {
  id: string;
  mode: "file";
  file: string;
  title: string;
  summary: string;
}

export interface RealInvoiceFetch {
  id: string;
  mode: "fetch";
  fetchId: string;
  title: string;
  summary: string;
  /** Expected truth on the hosted sandbox (system of record record). */
  expectedTotal: string;
  expectedVendor: string;
}

export type RealInvoice = RealInvoiceFile | RealInvoiceFetch;

/** Real invoice assets — same files used in backend tests and live fetch mode. */
export const REAL_INVOICES: RealInvoice[] = [
  {
    id: "acme-inv-001",
    mode: "file",
    file: "/invoices/acme-inv-001.txt",
    title: "Acme Corp — widget order",
    summary: "INV-001 · parsed from real invoice text · Total $1,240.00",
  },
  {
    id: "northwind-inv-12500",
    mode: "file",
    file: "/invoices/northwind-inv-12500.txt",
    title: "Northwind — platform license",
    summary: "INV-2026-0201 · Total $12,500.00 (over $10k policy ceiling)",
  },
  {
    id: "fetch-inv-2026-0042",
    mode: "fetch",
    fetchId: "INV-2026-0042",
    title: "Acme widgets Q1 (your ERP record)",
    summary: "Fetch mode — gate loads INV-2026-0042 from system of record · $3,610.00",
    expectedTotal: "3610.00",
    expectedVendor: "Acme Corp",
  },
];

export function getRealInvoice(id: string): RealInvoice | undefined {
  return REAL_INVOICES.find((inv) => inv.id === id);
}
