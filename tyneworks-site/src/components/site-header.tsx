import Link from "next/link";

const NAV = [
  { href: "/aanpak", label: "Aanpak" },
  { href: "/cases", label: "Cases" },
  { href: "/over", label: "Over" },
  { href: "/contact", label: "Contact" },
];

export function SiteHeader() {
  return (
    <header className="border-b border-brand-100 bg-white">
      <div className="container-tight flex items-center justify-between py-5">
        <Link href="/" className="flex items-center gap-2">
          <span className="inline-block h-6 w-6 rounded-md bg-brand-700" aria-hidden />
          <span className="font-bold text-brand-700 tracking-tight text-lg">Tyne Works</span>
        </Link>
        <nav className="hidden md:flex items-center gap-6">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className="text-sm text-ink hover:text-brand-700">
              {n.label}
            </Link>
          ))}
          <Link href="/contact" className="btn-primary text-sm py-2 px-4">
            Plan een sessie
          </Link>
        </nav>
      </div>
    </header>
  );
}
