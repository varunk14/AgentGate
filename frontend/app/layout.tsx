import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AgentGate",
  description:
    "Pre-action reliability gate for AI agents: verify a proposed action against caller-supplied evidence and get ALLOW / BLOCK / ESCALATE with a machine-readable reason.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
