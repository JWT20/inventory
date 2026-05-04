import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { getAllCases } from "@/lib/cases";

export const metadata = { title: "Cases — Tyne Works" };

export default function CasesPage() {
  const cases = getAllCases();

  return (
    <article className="container-tight py-16">
      <h1 className="text-3xl md:text-4xl heading-rule">Cases</h1>
      <p className="text-lg text-ink/80 max-w-prose mb-12">
        Mijn eerste klanten zaten in de wijnsector. De problemen — voorraad, klantadvies,
        ERP-randen — komen overal in retail en logistiek terug.
      </p>

      <div className="grid gap-6 md:grid-cols-2">
        {cases.map((c) => (
          <Link key={c.slug} href={`/cases/${c.slug}`} className="card-soft block hover:border-brand-200 transition">
            {c.label && (
              <span className="inline-block text-xs font-medium text-brand-700 bg-brand-50 px-2 py-0.5 rounded-full mb-3">
                {c.label}
              </span>
            )}
            <h2 className="text-xl mb-2">{c.title}</h2>
            <p className="text-sm text-ink/75">{c.summary}</p>
            <span className="inline-flex items-center gap-1 mt-4 text-sm text-brand-700">
              Lees meer <ArrowRight className="h-4 w-4" />
            </span>
          </Link>
        ))}
      </div>
    </article>
  );
}
