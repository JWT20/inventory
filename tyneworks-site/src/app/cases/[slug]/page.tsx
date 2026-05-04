import { notFound } from "next/navigation";
import Link from "next/link";
import { getCaseSlugs, getCaseMeta } from "@/lib/cases";

export function generateStaticParams() {
  return getCaseSlugs().map((slug) => ({ slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  try {
    const meta = getCaseMeta(slug);
    return { title: `${meta.title} — Tyne Works` };
  } catch {
    return { title: "Case — Tyne Works" };
  }
}

export default async function CasePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  let meta;
  try {
    meta = getCaseMeta(slug);
  } catch {
    notFound();
  }

  let MDXContent;
  try {
    MDXContent = (await import(`@/content/cases/${slug}.mdx`)).default;
  } catch {
    notFound();
  }

  return (
    <article className="container-tight py-16">
      <Link href="/cases" className="text-sm text-brand-700 hover:underline">
        ← Alle cases
      </Link>
      {meta!.label && (
        <span className="inline-block text-xs font-medium text-brand-700 bg-brand-50 px-2 py-0.5 rounded-full mt-4 mb-3">
          {meta!.label}
        </span>
      )}
      <h1 className="text-3xl md:text-4xl heading-rule">{meta!.title}</h1>
      <p className="text-lg text-ink/80 max-w-prose mb-10">{meta!.summary}</p>

      <div className="prose prose-headings:text-brand-700 prose-headings:font-bold prose-strong:text-ink max-w-prose">
        <MDXContent />
      </div>
    </article>
  );
}
