export const metadata = { title: "Contact — Tyne Works" };

export default function ContactPage() {
  return (
    <article className="container-tight py-16">
      <h1 className="text-3xl md:text-4xl heading-rule">Plan een AI-inspiratiesessie</h1>
      <p className="text-lg text-ink/80 max-w-prose mb-10">
        45 minuten, online of bij jou. Gratis en vrijblijvend. Je krijgt een eerlijk
        beeld van wat AI in jouw bedrijf kan betekenen.
      </p>

      <div className="grid md:grid-cols-2 gap-10">
        <div className="card-soft">
          <h2 className="text-xl mb-3">Direct boeken</h2>
          <p className="text-sm text-ink/70 mb-4">
            Kies zelf een moment. (Calendly-embed komt hier.)
          </p>
          <div className="aspect-[4/3] rounded-xl bg-brand-50 border border-brand-100 grid place-items-center text-brand-700/60 text-sm">
            Calendly placeholder
          </div>
        </div>

        <div className="card-soft">
          <h2 className="text-xl mb-3">Of stuur een bericht</h2>
          <p className="text-sm text-ink/70 mb-4">
            Liever eerst even mailen? Dat kan.
          </p>
          <p className="text-ink">
            <a href="mailto:jans@tyneworks.nl" className="text-brand-700 underline">
              jans@tyneworks.nl
            </a>
          </p>
        </div>
      </div>
    </article>
  );
}
