import type { NextConfig } from "next";

// The dashboard calls the AgentGate API directly from the browser (no proxy
// rewrite here on purpose, D40): a server-side rewrite hop would time out on
// free-tier backend cold starts; the browser waits and succeeds.
const nextConfig: NextConfig = {};

export default nextConfig;
