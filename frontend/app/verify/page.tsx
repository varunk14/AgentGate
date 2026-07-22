import { redirect } from "next/navigation";

export default async function VerifyRedirect({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const query = new URLSearchParams();
  if (params.invoice) query.set("invoice", params.invoice);
  if (params.mistake) query.set("mistake", params.mistake);
  if (params.sample === "tampered") {
    query.set("invoice", "acme-inv-001");
    query.set("mistake", "decimal");
  }
  if (params.sample === "clean") query.set("invoice", "acme-inv-001");
  const suffix = query.toString() ? `?${query.toString()}` : "";
  redirect(`/demo${suffix}`);
}
