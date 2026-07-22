import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AgentGate — Authorization layer for AI agent spending",
  description:
    "Verify every agent-proposed payment against invoice evidence before it executes. Deterministic allow, block, or escalate with machine-readable reasons.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
