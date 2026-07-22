"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  buildCallerVerifyRequest,
  buildFetchVerifyRequest,
  decimalSlipAmount,
  defaultProposal,
  parseInvoiceText,
  type AgentProposal,
  type ParsedInvoice,
} from "../lib/invoice-parser";
import { getRealInvoice, REAL_INVOICES, type RealInvoice } from "../lib/invoices";
import type { BlockReason, Decision, Money } from "../lib/types";

const API_BASE =
  process.env.NEXT_PUBLIC_AGENTGATE_API ?? "http://127.0.0.1:8000";
const TRACE_URL_TEMPLATE = process.env.NEXT_PUBLIC_TRACE_URL_TEMPLATE;

function moneyOrText(v: Money | string | null): string {
  if (v === null) return "";
  if (typeof v === "string") return v;
  return `${v.value} ${v.currency}`;
}

const BANNER_STYLES: Record<Decision["decision"], string> = {
  allow: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  block: "bg-red-500/15 text-red-300 border-red-500/40",
  escalate: "bg-amber-500/15 text-amber-300 border-amber-500/40",
};

function ReasonCard({ reason }: { reason: BlockReason }) {
  return (
    <li className="rounded-xl border border-white/10 bg-zinc-900/80 p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-zinc-100">{reason.check}</span>
        {reason.block_type && (
          <span className="rounded-full bg-white/5 px-2 py-0.5 font-mono text-xs text-zinc-400">
            {reason.block_type}
          </span>
        )}
      </div>
      <p className="mt-2 text-zinc-400">{reason.message}</p>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-xs text-zinc-500">
        {reason.expected !== null && (
          <>
            <dt>expected</dt>
            <dd className="text-zinc-200">{moneyOrText(reason.expected)}</dd>
          </>
        )}
        {reason.received !== null && (
          <>
            <dt>received</dt>
            <dd className="text-zinc-200">{moneyOrText(reason.received)}</dd>
          </>
        )}
        {reason.field_to_change && (
          <>
            <dt>field_to_change</dt>
            <dd className="text-zinc-200">{reason.field_to_change}</dd>
          </>
        )}
      </dl>
    </li>
  );
}

function VerifyDashboardInner() {
  const searchParams = useSearchParams();
  const [selectedId, setSelectedId] = useState<string>("acme-inv-001");
  const [parsed, setParsed] = useState<ParsedInvoice | null>(null);
  const [fetchMeta, setFetchMeta] = useState<RealInvoice & { mode: "fetch" } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [proposal, setProposal] = useState<AgentProposal | null>(null);
  const [includeRawText, setIncludeRawText] = useState(true);
  const [badGrounding, setBadGrounding] = useState(false);
  const [decimalSlip, setDecimalSlip] = useState(false);
  const [advancedBody, setAdvancedBody] = useState<string | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [attempt, setAttempt] = useState(1);

  const loadInvoiceFile = useCallback(async (filePath: string) => {
    setLoadError(null);
    setFetchMeta(null);
    const resp = await fetch(filePath);
    if (!resp.ok) throw new Error(`Could not load invoice file (${resp.status}).`);
    const text = await resp.text();
    const next = parseInvoiceText(text);
    setParsed(next);
    setProposal(defaultProposal(next));
    setSelectedId(
      REAL_INVOICES.find((i) => i.mode === "file" && i.file === filePath)?.id ?? "custom",
    );
  }, []);

  const loadFetchInvoice = useCallback((inv: RealInvoice & { mode: "fetch" }) => {
    setLoadError(null);
    setParsed(null);
    setFetchMeta(inv);
    setProposal({
      action_type: "approve_payment",
      amount_value: inv.expectedTotal,
      vendor: inv.expectedVendor,
      agent_rationale: "Payment matches fetched system-of-record invoice.",
    });
    setSelectedId(inv.id);
  }, []);

  const loadFromText = useCallback((text: string) => {
    setLoadError(null);
    setFetchMeta(null);
    try {
      const next = parseInvoiceText(text);
      setParsed(next);
      setProposal(defaultProposal(next));
      setSelectedId("custom");
    } catch (err) {
      setLoadError(String(err));
    }
  }, []);

  useEffect(() => {
    const invoiceId = searchParams.get("invoice");
    const mistake = searchParams.get("mistake");
    if (mistake === "decimal") setDecimalSlip(true);

    const pick = invoiceId ? getRealInvoice(invoiceId) : getRealInvoice("acme-inv-001");
    if (!pick) return;

    void (async () => {
      try {
        if (pick.mode === "file") await loadInvoiceFile(pick.file);
        else loadFetchInvoice(pick);
      } catch (err) {
        setLoadError(String(err));
      }
    })();
  }, [searchParams, loadInvoiceFile, loadFetchInvoice]);

  useEffect(() => {
    if (!proposal || !parsed) return;
    if (!decimalSlip) {
      setProposal((p) =>
        p ? { ...p, amount_value: parsed.total.value, vendor: parsed.vendor } : p,
      );
      return;
    }
    setProposal((p) =>
      p
        ? {
            ...p,
            amount_value: decimalSlipAmount(parsed.total.value),
            agent_rationale: "Agent misread comma grouping in total.",
          }
        : p,
    );
  }, [decimalSlip, parsed]);

  const requestBody = useMemo(() => {
    if (!proposal) return "";
    try {
      if (fetchMeta) {
        return JSON.stringify(
          buildFetchVerifyRequest(fetchMeta.fetchId, {
            ...proposal,
            invoice_number: fetchMeta.fetchId,
          }),
          null,
          2,
        );
      }
      if (parsed) {
        return JSON.stringify(
          buildCallerVerifyRequest(parsed, proposal, {
            include_raw_text: includeRawText,
            force_bad_grounding: badGrounding,
          }),
          null,
          2,
        );
      }
      return "";
    } catch {
      return "";
    }
  }, [fetchMeta, parsed, proposal, includeRawText, badGrounding]);

  useEffect(() => {
    setAdvancedBody(null);
  }, [requestBody]);

  async function verify(bodyOverride?: string) {
    const body = bodyOverride ?? advancedBody ?? requestBody;
    if (!body.trim()) return;

    setLoading(true);
    setError(null);
    setDecision(null);
    try {
      const resp = await fetch(`${API_BASE}/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      if (!resp.ok) {
        setError(`The API returned HTTP ${resp.status}. No verification decision was produced.`);
        return;
      }
      setDecision((await resp.json()) as Decision);
    } catch (err) {
      setError(
        `Could not reach the verification API (${String(err)}). ` +
          "Hosted sandbox may need up to a minute to wake from idle.",
      );
    } finally {
      setLoading(false);
    }
  }

  function applyFixAndResubmit() {
    if (!decision || !proposal) return;
    const reason = decision.reasons.find(
      (r) =>
        r.block_type === "agent_fixable" && r.field_to_change === "proposed_action.amount",
    );
    if (!reason || reason.expected === null) return;

    const expected =
      typeof reason.expected === "string"
        ? reason.expected.split(" ")[0]
        : reason.expected.value;

    const fixed: AgentProposal = {
      ...proposal,
      amount_value: expected,
      agent_rationale: "Corrected amount from gate feedback.",
    };

    setProposal(fixed);
    setDecimalSlip(false);
    setAttempt((n) => n + 1);

    if (parsed) {
      void verify(
        JSON.stringify(
          buildCallerVerifyRequest(parsed, fixed, {
            include_raw_text: includeRawText,
            force_bad_grounding: badGrounding,
          }),
          null,
          2,
        ),
      );
      return;
    }

    if (fetchMeta) {
      void verify(
        JSON.stringify(
          buildFetchVerifyRequest(fetchMeta.fetchId, {
            ...fixed,
            invoice_number: fetchMeta.fetchId,
          }),
          null,
          2,
        ),
      );
    }
  }

  async function onDropFile(file: File) {
    if (/\.pdf$/i.test(file.name) || file.type === "application/pdf") {
      setLoadError(null);
      try {
        const { extractPdfText } = await import("../lib/pdf-text");
        loadFromText(await extractPdfText(await file.arrayBuffer()));
      } catch (err) {
        setLoadError(String(err));
      }
      return;
    }
    if (!file.name.match(/\.(txt|text)$/i) && file.type && !file.type.includes("text")) {
      setLoadError("Drop a plain-text invoice (.txt) or a digital PDF with a text layer.");
      return;
    }
    loadFromText(await file.text());
  }

  const traceUrl =
    decision?.trace_id && TRACE_URL_TEMPLATE
      ? TRACE_URL_TEMPLATE.replace("{id}", decision.trace_id)
      : null;

  const canFix =
    decision?.decision === "block" &&
    decision.reasons.some(
      (r) => r.block_type === "agent_fixable" && r.field_to_change === "proposed_action.amount",
    );

  return (
    <div data-testid="verify-dashboard">
      <header className="border-b border-white/10 pb-8">
        <p className="text-sm font-medium uppercase tracking-wider text-violet-400">Live product demo</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight text-white">
          Verify real invoices against the gate
        </h1>
        <p className="mt-3 max-w-3xl text-sm leading-relaxed text-zinc-400">
          Load actual invoice files from this repo, paste email text, or use fetch mode against
          the hosted system-of-record record. The gate receives the same JSON your production
          integration sends — no canned dummy payloads.
        </p>
      </header>

      <section className="mt-8">
        <h2 className="text-sm font-medium uppercase tracking-wide text-zinc-500">1 · Choose evidence</h2>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          {REAL_INVOICES.map((inv) => (
            <button
              key={inv.id}
              type="button"
              data-testid={`invoice-${inv.id}`}
              onClick={() => {
                setAttempt(1);
                setDecision(null);
                if (inv.mode === "file") void loadInvoiceFile(inv.file);
                else loadFetchInvoice(inv);
              }}
              className={`rounded-xl border p-4 text-left transition ${
                selectedId === inv.id
                  ? "border-violet-500/60 bg-violet-500/10"
                  : "border-white/10 bg-zinc-900/50 hover:border-white/20"
              }`}
            >
              <p className="font-medium text-white">{inv.title}</p>
              <p className="mt-2 text-xs leading-relaxed text-zinc-500">{inv.summary}</p>
            </button>
          ))}
        </div>

        <div
          data-testid="invoice-dropzone"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            const file = e.dataTransfer.files[0];
            if (file) void onDropFile(file);
          }}
          className="mt-4 rounded-xl border border-dashed border-white/15 bg-zinc-950/50 p-6 text-center"
        >
          <p className="text-sm text-zinc-300">Drag and drop a real invoice .txt or .pdf file</p>
          <p className="mt-1 text-xs text-zinc-500">
            Or paste invoice text below · scanned PDFs need OCR upstream
          </p>
          <label className="mt-3 inline-block cursor-pointer text-xs text-violet-300 hover:text-violet-200">
            Browse file
            <input
              type="file"
              accept=".txt,text/plain,.pdf,application/pdf"
              data-testid="invoice-upload"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void onDropFile(file);
              }}
            />
          </label>
        </div>

        <textarea
          data-testid="invoice-paste"
          placeholder="Paste invoice email or PDF-extracted text here…"
          className="mt-3 h-32 w-full rounded-xl border border-white/10 bg-zinc-950 p-3 font-mono text-xs text-zinc-300 outline-none focus:border-violet-500/50"
          onBlur={(e) => {
            if (e.target.value.trim()) loadFromText(e.target.value);
          }}
        />

        {loadError && (
          <p data-testid="invoice-load-error" className="mt-3 text-sm text-red-400">
            {loadError}
          </p>
        )}
      </section>

      {(parsed || fetchMeta) && proposal && (
        <section className="mt-10">
          <h2 className="text-sm font-medium uppercase tracking-wide text-zinc-500">
            2 · Agent proposal (editable)
          </h2>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <div className="rounded-xl border border-white/10 bg-zinc-900/40 p-4 text-sm">
              <p className="font-medium text-white">Parsed evidence</p>
              {parsed && (
                <dl className="mt-3 space-y-1 text-zinc-400">
                  <div className="flex justify-between gap-4">
                    <dt>Invoice</dt>
                    <dd className="font-mono text-zinc-200">{parsed.invoice_number}</dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt>Vendor</dt>
                    <dd className="text-zinc-200">{parsed.vendor}</dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt>Total</dt>
                    <dd className="font-mono text-zinc-200">
                      {parsed.total.value} {parsed.total.currency}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt>Line items</dt>
                    <dd className="text-zinc-200">{parsed.line_items.length}</dd>
                  </div>
                </dl>
              )}
              {fetchMeta && (
                <dl className="mt-3 space-y-1 text-zinc-400">
                  <div className="flex justify-between gap-4">
                    <dt>Fetch</dt>
                    <dd className="font-mono text-zinc-200">{fetchMeta.fetchId}</dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt>Stored total</dt>
                    <dd className="font-mono text-zinc-200">${fetchMeta.expectedTotal} USD</dd>
                  </div>
                  <p className="mt-3 text-xs text-zinc-500">
                    Evidence is loaded by the gate from the operator&apos;s system of record — the
                    agent never supplies the invoice body.
                  </p>
                </dl>
              )}
            </div>

            <div className="rounded-xl border border-white/10 bg-zinc-900/40 p-4">
              <label className="block text-xs text-zinc-500">Payment amount (USD)</label>
              <input
                data-testid="proposal-amount"
                value={proposal.amount_value}
                onChange={(e) =>
                  setProposal({ ...proposal, amount_value: e.target.value, agent_rationale: "Manual edit." })
                }
                className="mt-1 w-full rounded-lg border border-white/10 bg-zinc-950 px-3 py-2 font-mono text-sm text-white outline-none focus:border-violet-500/50"
              />

              <label className="mt-4 block text-xs text-zinc-500">Action type</label>
              <select
                data-testid="proposal-action-type"
                value={proposal.action_type}
                onChange={(e) =>
                  setProposal({
                    ...proposal,
                    action_type: e.target.value as AgentProposal["action_type"],
                  })
                }
                className="mt-1 w-full rounded-lg border border-white/10 bg-zinc-950 px-3 py-2 text-sm text-white outline-none"
              >
                <option value="approve_payment">approve_payment</option>
                <option value="reject">reject</option>
              </select>

              {parsed && (
                <>
                  <label className="mt-4 flex items-center gap-2 text-xs text-zinc-400">
                    <input
                      type="checkbox"
                      data-testid="include-raw-text"
                      checked={includeRawText}
                      onChange={(e) => setIncludeRawText(e.target.checked)}
                    />
                    Attach original invoice text for grounding
                  </label>
                  <label className="mt-2 flex items-center gap-2 text-xs text-zinc-400">
                    <input
                      type="checkbox"
                      data-testid="bad-grounding"
                      checked={badGrounding}
                      onChange={(e) => setBadGrounding(e.target.checked)}
                    />
                    Attach unrelated source text (grounding failure demo)
                  </label>
                  <label className="mt-2 flex items-center gap-2 text-xs text-zinc-400">
                    <input
                      type="checkbox"
                      data-testid="decimal-slip"
                      checked={decimalSlip}
                      onChange={(e) => setDecimalSlip(e.target.checked)}
                    />
                    Simulate agent decimal slip (e.g. $1,240 → $12,400)
                  </label>
                </>
              )}
            </div>
          </div>

          <div className="mt-6 flex flex-wrap items-center gap-3">
            <button
              type="button"
              data-testid="verify"
              disabled={loading || !requestBody}
              onClick={() => verify()}
              className="rounded-lg bg-violet-600 px-5 py-2 text-sm font-medium text-white hover:bg-violet-500 disabled:opacity-50"
            >
              {loading ? "Running verification…" : `Run verification (attempt ${attempt})`}
            </button>
            {canFix && (
              <button
                type="button"
                data-testid="apply-fix"
                onClick={applyFixAndResubmit}
                className="rounded-lg border border-emerald-500/40 px-5 py-2 text-sm text-emerald-300 hover:bg-emerald-500/10"
              >
                Apply gate fix & resubmit
              </button>
            )}
          </div>
        </section>
      )}

      <section className="mt-10 grid gap-6 lg:grid-cols-2">
        <div className="rounded-2xl border border-white/10 bg-zinc-900/30 p-5">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium uppercase tracking-wide text-zinc-500">Decision</h2>
          </div>

          {error && (
            <div data-testid="error-panel" className="mt-4 rounded-xl border border-white/10 bg-zinc-950 p-4 text-sm text-zinc-300">
              {error}
            </div>
          )}

          {!decision && !error && (
            <p className="mt-4 text-sm text-zinc-600">Run verification to see the live gate response.</p>
          )}

          {decision && (
            <div className="mt-4 space-y-4">
              <div className="flex flex-wrap items-center gap-4">
                <span
                  data-testid="decision-banner"
                  className={`rounded-lg border px-4 py-2 font-mono text-lg font-semibold uppercase ${BANNER_STYLES[decision.decision]}`}
                >
                  {decision.decision}
                </span>
                <div className="text-sm text-zinc-400">
                  Score{" "}
                  <span data-testid="score" className="font-mono text-zinc-100">
                    {decision.score === null ? "not computed" : decision.score}
                  </span>
                </div>
              </div>

              {decision.reasons.length > 0 && (
                <ul data-testid="reasons" className="space-y-2">
                  {decision.reasons.map((r, i) => (
                    <ReasonCard key={`${r.check}-${i}`} reason={r} />
                  ))}
                </ul>
              )}

              {decision.checks.length > 0 && (
                <div className="overflow-x-auto rounded-xl border border-white/10">
                  <table data-testid="checks-table" className="w-full border-collapse text-left text-xs">
                    <thead>
                      <tr className="border-b border-white/10 bg-zinc-950/80 text-zinc-500">
                        <th className="px-3 py-2 font-medium">Check</th>
                        <th className="px-3 py-2 font-medium">Type</th>
                        <th className="px-3 py-2 font-medium">Result</th>
                        <th className="px-3 py-2 font-medium">Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {decision.checks.map((c) => (
                        <tr key={c.name} className="border-b border-white/5 align-top">
                          <td className="px-3 py-2 font-mono text-zinc-200">{c.name}</td>
                          <td className="px-3 py-2 text-zinc-500">{c.type}</td>
                          <td className={`px-3 py-2 font-mono ${c.passed ? "text-emerald-400" : "text-red-400"}`}>
                            {c.passed ? "pass" : "fail"}
                          </td>
                          <td className="px-3 py-2 font-mono text-zinc-500">{c.detail}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 rounded-xl border border-white/10 bg-zinc-950/50 p-4 text-xs text-zinc-500">
                <dt>Evidence used</dt>
                <dd className="font-mono text-zinc-300">
                  {decision.evidence_used.join(", ") || "none"}
                </dd>
                <dt>Trace ID</dt>
                <dd className="font-mono text-zinc-300">
                  {traceUrl ? (
                    <a href={traceUrl} target="_blank" rel="noreferrer" data-testid="trace-id" className="underline">
                      {decision.trace_id}
                    </a>
                  ) : (
                    <span data-testid="trace-id">{decision.trace_id ?? "none"}</span>
                  )}
                </dd>
                <dt>Latency</dt>
                <dd className="font-mono text-zinc-300">
                  {decision.latency_ms !== null ? `${decision.latency_ms} ms` : "n/a"}
                </dd>
              </dl>
            </div>
          )}
        </div>

        <div className="rounded-2xl border border-white/10 bg-zinc-900/30 p-5">
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="text-sm font-medium text-zinc-400 hover:text-white"
          >
            {showAdvanced ? "Hide" : "Show"} API request JSON
          </button>
          {showAdvanced && (
            <>
              <textarea
                data-testid="request-body"
                value={advancedBody ?? requestBody}
                onChange={(e) => setAdvancedBody(e.target.value)}
                className="mt-3 h-80 w-full rounded-xl border border-white/10 bg-zinc-950 p-3 font-mono text-xs text-zinc-300 outline-none focus:border-violet-500/50"
              />
              <button
                type="button"
                data-testid="verify-raw-json"
                disabled={loading}
                onClick={() => verify()}
                className="mt-3 rounded-lg border border-white/15 px-4 py-2 text-xs text-zinc-300 hover:border-white/30"
              >
                Send edited JSON
              </button>
            </>
          )}
        </div>
      </section>

      <p className="mt-8 text-xs text-zinc-600">
        Connected API: <span data-testid="api-base" className="font-mono text-zinc-500">{API_BASE}</span>
      </p>
    </div>
  );
}

export function VerifyDashboard() {
  return (
    <Suspense fallback={null}>
      <VerifyDashboardInner />
    </Suspense>
  );
}
