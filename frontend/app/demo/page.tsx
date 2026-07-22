import { SiteFooter } from "../../components/SiteFooter";
import { SiteNav } from "../../components/SiteNav";
import { VerifyDashboard } from "../../components/VerifyDashboard";

export default function DemoPage() {
  return (
    <>
      <SiteNav active="demo" />
      <main className="mx-auto max-w-6xl px-6 py-10">
        <VerifyDashboard />
      </main>
      <SiteFooter />
    </>
  );
}
