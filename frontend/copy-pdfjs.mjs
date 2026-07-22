/**
 * Copy pdf.js's prebuilt browser bundles from node_modules into public/pdfjs/
 * so the app can load them with a native (bundler-free) dynamic import —
 * webpack cannot process pdfjs-dist v5's builds. Runs on postinstall; the
 * copies are gitignored.
 */
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const src = join(root, "node_modules", "pdfjs-dist", "build");
const dest = join(root, "public", "pdfjs");

mkdirSync(dest, { recursive: true });
for (const file of ["pdf.min.mjs", "pdf.worker.min.mjs"]) {
  copyFileSync(join(src, file), join(dest, file));
}
console.log(`copied pdf.js bundles to ${dest}`);
