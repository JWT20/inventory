import Link from "next/link";
import { ArrowRight } from "lucide-react";

export const metadata = { title: "Aanpak — Tyne Works" };

export default function AanpakPage() {
  return (
    <article className="container-tight py-16">
      <h1 className="text-3xl md:text-4xl heading-rule">Aanpak</h1>
      <p className="text-lg text-ink/80 max-w-prose mb-12">
        Van een eerste verkennend gesprek tot werkende software — in vier stappen.
        Geen vendor lock-in, geen abonnement: jij bent eigenaar van wat we bouwen.
      </p>

      <ol className="space-y-10">
        <Step
          n={1}
          title="AI-inspiratiesessie"
          body="Gratis kennismaking van 45 minuten. We kijken samen naar je bedrijf en bespreken waar AI concreet iets kan toevoegen — en waar niet. Geen verkoopgesprek."
        />
        <Step
          n={2}
          title="Voorstel"
          body="Scope, planning en vaste prijs. Geen open einde, geen uurtje-factuurtje. Je weet vooraf wat je krijgt en wat het kost."
        />
        <Step
          n={3}
          title="Bouwen"
          body="Iteratief, met regelmatig overleg. Je ziet tussentijds werkende versies en kan bijsturen."
        />
        <Step
          n={4}
          title="Oplevering"
          body="Inclusief overdracht, korte training en documentatie. De code is van jou."
        />
      </ol>

      <div className="callout mt-16">
        <h2 className="text-2xl mb-2">Klaar voor stap 1?</h2>
        <p className="text-ink/80 mb-6">
          De inspiratiesessie kost niets en verplicht tot niets.
        </p>
        <Link href="/contact" className="btn-primary">
          Plan een sessie <ArrowRight className="h-4 w-4" />
        </Link>
      </div>
    </article>
  );
}

function Step({ n, title, body }: { n: number; title: string; body: string }) {
  return (
    <li className="grid grid-cols-[auto_1fr] gap-6">
      <span className="text-4xl font-bold text-brand-700/30">{String(n).padStart(2, "0")}</span>
      <div>
        <h3 className="text-xl mb-2">{title}</h3>
        <p className="text-ink/80 max-w-prose">{body}</p>
      </div>
    </li>
  );
}
