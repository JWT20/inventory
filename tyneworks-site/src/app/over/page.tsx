export const metadata = { title: "Over — Tyne Works" };

export default function OverPage() {
  return (
    <article className="container-tight py-16">
      <h1 className="text-3xl md:text-4xl heading-rule">Over Tyne Works</h1>

      <div className="grid md:grid-cols-[200px_1fr] gap-10 items-start">
        <div className="aspect-square rounded-2xl bg-brand-100 border border-brand-200 grid place-items-center text-brand-700/60 text-sm">
          Foto
        </div>
        <div className="space-y-5 text-ink/85 max-w-prose">
          <p>
            Tyne Works is het bedrijf van <strong>Jans Tigelaar</strong>. Ik help MKB-bedrijven
            in logistiek en retail met maatwerk AI-automatisering.
          </p>
          <p>
            Mijn werk zit op een specifiek punt: daar waar generieke software ophoudt en
            de werkelijkheid begint. Dat is meestal een Excel-werkblad rond het ERP, een ervaren
            collega die "even weet hoe het zit", of een proces dat niet in een kant-en-klaar
            pakket past.
          </p>
          <p>
            Ik maak concreet wat AI is en wat het in jouw bedrijf kan betekenen — en bouw
            het vervolgens, in werkende software. Niet meer, niet minder.
          </p>
          <p className="pt-4 text-sm text-ink/60">
            jans@tyneworks.nl
          </p>
        </div>
      </div>
    </article>
  );
}
