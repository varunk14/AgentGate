/**
 * Extract plain text from a digital PDF's text layer, reconstructing line
 * breaks and column spacing from glyph coordinates. Deterministic — no OCR,
 * no LLM. Scanned (image-only) PDFs have no text layer and are rejected
 * with an instructive error; OCR belongs upstream of the gate.
 */

const MAX_PDF_BYTES = 10 * 1024 * 1024;

/** Y-distance (PDF units) within which two glyph runs count as one line. */
const LINE_TOLERANCE = 2.5;

interface PositionedRun {
  str: string;
  x: number;
  y: number;
  width: number;
}

interface PdfTextItem {
  str?: string;
  transform: number[];
  width: number;
}

interface PdfJsModule {
  GlobalWorkerOptions: { workerSrc: string };
  getDocument(opts: { data: Uint8Array }): {
    promise: Promise<{
      numPages: number;
      getPage(n: number): Promise<{
        getTextContent(): Promise<{ items: PdfTextItem[] }>;
      }>;
      destroy(): Promise<void>;
    }>;
  };
}

/**
 * Load pdf.js's own prebuilt browser bundle from public/pdfjs/ with a native
 * dynamic import. Webpack cannot process pdfjs-dist v5's builds (module
 * evaluation throws), so the bundler is deliberately bypassed; the files are
 * copied from node_modules on postinstall and served same-origin.
 */
async function loadPdfJs(): Promise<PdfJsModule> {
  const src = "/pdfjs/pdf.min.mjs";
  const pdfjs = (await import(/* webpackIgnore: true */ src)) as PdfJsModule;
  pdfjs.GlobalWorkerOptions.workerSrc = "/pdfjs/pdf.worker.min.mjs";
  return pdfjs;
}

function runCharWidth(run: PositionedRun): number {
  return run.str.length > 0 ? run.width / run.str.length : 0;
}

/** Rebuild one text line, converting horizontal gaps back into spaces. */
function joinLine(runs: PositionedRun[]): string {
  const sorted = [...runs].sort((a, b) => a.x - b.x);
  let line = "";
  let prev: PositionedRun | null = null;
  for (const run of sorted) {
    if (prev) {
      const gap = run.x - (prev.x + prev.width);
      const charWidth = runCharWidth(prev) || runCharWidth(run) || 1;
      let spaces = Math.round(gap / charWidth);
      if (spaces < 1 && gap > charWidth * 0.25) spaces = 1;
      if (spaces > 0) line += " ".repeat(spaces);
    }
    line += run.str;
    prev = run;
  }
  return line.trimEnd();
}

export async function extractPdfText(data: ArrayBuffer): Promise<string> {
  if (data.byteLength > MAX_PDF_BYTES) {
    throw new Error("PDF is larger than 10 MB — attach the invoice pages only.");
  }

  const pdfjs = await loadPdfJs();
  const doc = await pdfjs.getDocument({ data: new Uint8Array(data) }).promise;
  const pages: string[] = [];
  try {
    for (let pageNo = 1; pageNo <= doc.numPages; pageNo++) {
      const page = await doc.getPage(pageNo);
      const content = await page.getTextContent();

      const runs: PositionedRun[] = [];
      for (const item of content.items) {
        const str = item.str;
        if (!str?.trim()) continue;
        runs.push({
          str,
          x: item.transform[4],
          y: item.transform[5],
          width: item.width,
        });
      }

      const lines: PositionedRun[][] = [];
      for (const run of [...runs].sort((a, b) => b.y - a.y)) {
        const current = lines[lines.length - 1];
        if (current && Math.abs(current[0].y - run.y) <= LINE_TOLERANCE) {
          current.push(run);
        } else {
          lines.push([run]);
        }
      }
      pages.push(lines.map(joinLine).join("\n"));
    }
  } finally {
    await doc.destroy();
  }

  const text = pages.join("\n").trim();
  if (!text) {
    throw new Error(
      "This PDF has no text layer (likely a scan). OCR belongs upstream of the gate — paste the extracted text instead.",
    );
  }
  return text;
}
