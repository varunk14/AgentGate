import Link from "next/link";
import { FOOTER_LINKS, GITHUB_URL, SITE } from "../lib/site";

export function SiteFooter() {
  return (
    <footer className="border-t border-white/10 bg-zinc-950">
      <div className="mx-auto grid max-w-6xl gap-10 px-6 py-12 md:grid-cols-4">
        <div className="md:col-span-2">
          <p className="text-lg font-semibold text-white">{SITE.name}</p>
          <p className="mt-2 max-w-md text-sm leading-relaxed text-zinc-400">
            {SITE.description} Built for teams deploying AI agents that touch real money.
          </p>
        </div>

        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-zinc-500">Product</p>
          <ul className="mt-3 space-y-2">
            {FOOTER_LINKS.product.map((link) => (
              <li key={link.href}>
                <Link href={link.href} className="text-sm text-zinc-400 hover:text-white">
                  {link.label}
                </Link>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-zinc-500">Developers</p>
          <ul className="mt-3 space-y-2">
            {FOOTER_LINKS.developers.map((link) => (
              <li key={link.href}>
                <Link
                  href={link.href}
                  target={link.href.startsWith("http") ? "_blank" : undefined}
                  rel={link.href.startsWith("http") ? "noreferrer" : undefined}
                  className="text-sm text-zinc-400 hover:text-white"
                >
                  {link.label}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="border-t border-white/5 px-6 py-4">
        <div className="mx-auto flex max-w-6xl flex-col gap-2 text-xs text-zinc-600 sm:flex-row sm:items-center sm:justify-between">
          <p>© {new Date().getFullYear()} {SITE.name}. MIT License.</p>
          <p>
            Passing the gate means consistent with evidence — not authorized payment.{" "}
            <Link href={GITHUB_URL} className="underline hover:text-zinc-400">
              Threat model
            </Link>
          </p>
        </div>
      </div>
    </footer>
  );
}
