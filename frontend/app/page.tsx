"use client";

// The dashboard is a dumb pipe over POST /verify (D39): the request box is
// sent VERBATIM (the gate is the validator — pasted garbage demonstrates the
// fail-closed contract live), and the response renders the wire truth: string
// decimals verbatim (never parseFloat, D1), score null as "not computed"
// (never 0, D32), and a transport failure as an error — never a synthesized
// decision.

import { useState } from "react";
import { SAMPLES } from "../lib/samples";
import type { BlockReason, Decision, Money } from "../lib/types";

const API_BASE =
  process.env.NEXT_PUBLIC_AGENTGATE_API ?? "http://127.0.0.1:8000";
// e.g. "https://cloud.langfuse.com/project/<id>/traces/{id}". Unset => the
// trace id stays plain text: no dead links to an unconfigured Langfuse.
const TRACE_URL_TEMPLATE = process.env.NEXT_PUBLIC_TRACE_URL_TEMPLATE;

function moneyOrText(v: Money | string | null): string {
  if (v === null) return "";
  if (typeof v === "string") return v;
  return `${v.value} ${v.currency}`;
}

const BANNER_STYLES: Record<Decision["decision"], string> = {
  allow: "bg-emerald-950 text-emerald-300 border-emerald-700",
  block: "bg-red-950 text-red-300 border-red-700",
  escalate: "bg-amber-950 text-amber-300 border-amber-700",
};

function ReasonCard({ reason }: { reason: BlockReason }) {
  return (
    <li className="rounded-md border border-zinc-800 bg-zinc-900 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-zinc-200">{reason.check}</span>
        {reason.block_type && (
          <span className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-xs text-zinc-400">
            {reason.block_type}
          </span>
        )}
      </div>
      <p className="mt-1 text-zinc-400">{reason.message}</p>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono text-xs text-zinc-500">
        {reason.expected !== null && (
          <>
            <dt>expected</dt>
            <dd className="text-zinc-300">{moneyOrText(reason.expected)}</dd>
          </>
        )}
        {reason.received !== null && (
          <>
            <dt>received</dt>
            <dd className="text-zinc-300">{moneyOrText(reason.received)}</dd>
          </>
        )}
        {reason.field_to_change && (
          <>
            <dt>field_to_change</dt>
            <dd className="text-zinc-300">{reason.field_to_change}</dd>
          </>
        )}
      </dl>
    </li>
  );
}

export default function Home() {
  const [body, setBody] = useState("");
  const [decision, setDecision] = useState<Decision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function verify() {
    setLoading(true);
    setError(null);
    setDecision(null);
    try {
      const resp = await fetch(`${API_BASE}/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body, // verbatim: what you see in the box is what the gate judges
      });
      if (!resp.ok) {
        // POST /verify always answers 200 with a Decision (D35); anything
        // else is a transport-level problem, not a verdict.
        setError(`The gate answered HTTP ${resp.status}. That is a transport problem — no decision was made.`);
        return;
      }
      setDecision((await resp.json()) as Decision);
    } catch (err) {
      setError(
        `Could not reach the gate at ${API_BASE} (${String(err)}). No decision was made. ` +
          "Free-tier backends can take about a minute to wake up — try again.",
      );
    } finally {
      setLoading(false);
    }
  }

  async function loadFile(file: File | undefined) {
    if (file) setBody(await file.text());
  }

  const traceUrl =
    decision?.trace_id && TRACE_URL_TEMPLATE
      ? TRACE_URL_TEMPLATE.replace("{id}", decision.trace_id)
      : null;

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <header className="border-b border-zinc-800 pb-4">
        <h1 className="text-2xl font-semibold tracking-tight">AgentGate</h1>
        <p className="mt-1 max-w-3xl text-sm text-zinc-400">
          A pre-action reliability gate for AI agents. It checks a proposed
          action against the evidence the caller supplies and answers allow,
          block, or escalate. Passing means &quot;consistent with the evidence
          provided&quot; — never &quot;the payment is correct or authorized.&quot;
        </p>
      </header>

      <div className="mt-6 grid gap-8 lg:grid-cols-2">
        <section>
          <h2 className="text-sm font-medium uppercase tracking-wide text-zinc-500">
            Request
          </h2>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {SAMPLES.map((s) => (
              <button
                key={s.id}
                type="button"
                data-testid={`sample-${s.id}`}
                title={`Expected: ${s.expects}`}
                onClick={() => setBody(JSON.stringify(s.body, null, 2))}
                className="rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-1 text-xs text-zinc-300 hover:border-zinc-500"
              >
                {s.label}
              </button>
            ))}
          </div>
          <textarea
            data-testid="request-body"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            spellCheck={false}
            placeholder='Paste a VerifyRequest body: { "proposed_action": { ... }, "source": { "invoice": { ... }, "raw_text": "optional" } } — or load a sample above.'
            className="mt-3 h-96 w-full resize-y rounded-md border border-zinc-800 bg-zinc-900 p-3 font-mono text-xs text-zinc-200 outline-none focus:border-zinc-600"
          />
          <div className="mt-3 flex items-center gap-3">
            <button
              type="button"
              data-testid="verify"
              onClick={verify}
              disabled={loading}
              className="rounded-md bg-zinc-100 px-4 py-1.5 text-sm font-medium text-zinc-950 hover:bg-white disabled:opacity-50"
            >
              {loading ? "Verifying..." : "Verify"}
            </button>
            <label className="text-xs text-zinc-400">
              or upload a .json file{" "}
              <input
                type="file"
                accept=".json,application/json"
                data-testid="upload"
                onChange={(e) => void loadFile(e.target.files?.[0])}
                className="ml-1 text-xs file:mr-2 file:rounded-md file:border file:border-zinc-700 file:bg-zinc-900 file:px-2 file:py-1 file:text-xs file:text-zinc-300"
              />
            </label>
          </div>
        </section>

        <section>
          <h2 className="text-sm font-medium uppercase tracking-wide text-zinc-500">
            Decision
          </h2>

          {error && (
            <div
              data-testid="error-panel"
              className="mt-3 rounded-md border border-zinc-700 bg-zinc-900 p-3 text-sm text-zinc-300"
            >
              {error}
            </div>
          )}

          {!decision && !error && (
            <p className="mt-3 text-sm text-zinc-600">
              No request sent yet. Load a sample and press Verify.
            </p>
          )}

          {decision && (
            <div className="mt-3 space-y-4">
              <div className="flex items-center gap-4">
                <span
                  data-testid="decision-banner"
                  className={`rounded-md border px-4 py-1.5 font-mono text-lg font-semibold ${BANNER_STYLES[decision.decision]}`}
                >
                  {decision.decision}
                </span>
                <div className="text-sm text-zinc-400">
                  score{" "}
                  <span data-testid="score" className="font-mono text-zinc-200">
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
                <table
                  data-testid="checks-table"
                  className="w-full border-collapse text-left text-xs"
                >
                  <thead>
                    <tr className="border-b border-zinc-800 text-zinc-500">
                      <th className="py-1.5 pr-2 font-medium">check</th>
                      <th className="py-1.5 pr-2 font-medium">type</th>
                      <th className="py-1.5 pr-2 font-medium">result</th>
                      <th className="py-1.5 font-medium">detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {decision.checks.map((c) => (
                      <tr key={c.name} className="border-b border-zinc-900 align-top">
                        <td className="py-1.5 pr-2 font-mono text-zinc-300">{c.name}</td>
                        <td className="py-1.5 pr-2 text-zinc-500">{c.type}</td>
                        <td
                          className={`py-1.5 pr-2 font-mono ${c.passed ? "text-emerald-400" : "text-red-400"}`}
                        >
                          {c.passed ? "pass" : "fail"}
                        </td>
                        <td className="py-1.5 font-mono text-zinc-500">{c.detail}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs text-zinc-500">
                <dt>evidence</dt>
                <dd className="font-mono text-zinc-400">
                  {decision.evidence_used.join(", ") || "none"}
                </dd>
                <dt>trace</dt>
                <dd className="font-mono text-zinc-400">
                  {traceUrl ? (
                    <a
                      href={traceUrl}
                      target="_blank"
                      rel="noreferrer"
                      data-testid="trace-id"
                      className="underline decoration-zinc-600 hover:text-zinc-200"
                    >
                      {decision.trace_id}
                    </a>
                  ) : (
                    <span data-testid="trace-id">{decision.trace_id ?? "none"}</span>
                  )}
                </dd>
                <dt>latency</dt>
                <dd className="font-mono text-zinc-400">
                  {decision.latency_ms !== null ? `${decision.latency_ms} ms` : "n/a"}
                </dd>
                <dt>timestamp</dt>
                <dd className="font-mono text-zinc-400">{decision.timestamp ?? "n/a"}</dd>
              </dl>
            </div>
          )}
        </section>
      </div>

      <footer className="mt-10 border-t border-zinc-800 pt-3 text-xs text-zinc-600">
        Gate API: <span data-testid="api-base" className="font-mono">{API_BASE}</span>
        {" - "}decisions are verified against caller-supplied evidence; see the
        project README for the threat model.
      </footer>
    </main>
  );
}
