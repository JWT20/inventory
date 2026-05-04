import Link from "next/link";

export function SiteFooter() {
  return (
    <footer className="border-t border-brand-100 bg-brand-50/40 mt-16">
      <div className="container-tight py-10 grid gap-6 md:grid-cols-3">
        <div>
          <div className="flex items-center gap-2 mb-2">
            <span className="inline-block h-5 w-5 rounded-md bg-brand-700" aria-hidden />
            <span className="font-bold text-brand-700">Tyne Works</span>
          </div>
          <p className="text-sm text-ink/70 max-w-xs">
            Maatwerk AI-automatisering voor MKB.
          </p>
        </div>
        <div className="text-sm">
          <p className="font-semibold mb-2 text-brand-700">Site</p>
          <ul className="space-y-1">
            <li><Link href="/aanpak" className="hover:text-brand-700">Aanpak</Link></li>
            <li><Link href="/cases" className="hover:text-brand-700">Cases</Link></li>
            <li><Link href="/over" className="hover:text-brand-700">Over</Link></li>
            <li><Link href="/contact" className="hover:text-brand-700">Contact</Link></li>
          </ul>
        </div>
        <div className="text-sm">
          <p className="font-semibold mb-2 text-brand-700">Contact</p>
          <p className="text-ink/70">jans@tyneworks.nl</p>
        </div>
      </div>
      <div className="border-t border-brand-100">
        <div className="container-tight py-4 text-xs text-ink/60 flex justify-between">
          <span>© {new Date().getFullYear()} Tyne Works</span>
          <span>KvK · BTW</span>
        </div>
      </div>
    </footer>
  );
}
