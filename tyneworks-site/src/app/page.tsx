import Link from "next/link";
import { ArrowRight, ScanLine, FileText, Users, Boxes, Brain, Settings2 } from "lucide-react";

export default function HomePage() {
  return (
    <>
      {/* Hero */}
      <section className="container-tight pt-20 pb-16">
        <p className="text-sm font-medium text-brand-700 mb-4">Tyne Works</p>
        <h1 className="text-4xl md:text-5xl leading-tight max-w-3xl">
          Stop met losse Excel-bestanden rond je ERP.
        </h1>
        <p className="mt-6 text-lg text-ink/80 max-w-2xl">
          Maatwerk AI-automatisering voor MKB. Ik vertaal jouw bedrijfslogica
          naar werkende software — daar waar standaardpakketten ophouden.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link href="/contact" className="btn-primary">
            Plan een AI-inspiratiesessie <ArrowRight className="h-4 w-4" />
          </Link>
          <Link href="/cases" className="btn-ghost">
            Bekijk voorbeelden
          </Link>
        </div>
      </section>

      {/* 3 problemen */}
      <section className="container-tight py-16">
        <h2 className="heading-rule text-2xl">Waar ik mee help</h2>
        <div className="grid gap-6 md:grid-cols-3">
          <Problem
            Icon={ScanLine}
            title="Voorraad & visuele herkenning"
            body="Producten automatisch identificeren en voorraad bijhouden zonder handmatig scannen."
          />
          <Problem
            Icon={FileText}
            title="Factuur- en PDF-verwerking"
            body="Inkomende documenten omzetten naar gestructureerde data in je boekhouding of ERP."
          />
          <Problem
            Icon={Users}
            title="Klantprofielen & advies"
            body="Uit klantdata samenhangende profielen voor gepersonaliseerd advies en aanbod."
          />
        </div>
      </section>

      {/* Waar ik thuis ben — placeholder scenario's */}
      <section className="container-tight py-16">
        <h2 className="heading-rule text-2xl">Waar ik thuis ben</h2>
        <div className="grid gap-6 md:grid-cols-3">
          <Scenario
            Icon={Settings2}
            title="Excel rond het ERP"
            body="Het bestelproces, de prijslijsten, de uitzonderingen — het draait al jaren op een onderhouden Excel. Ik bouw daar een betrouwbaar systeem omheen."
          />
          <Scenario
            Icon={Brain}
            title="Kennis die in hoofden zit"
            body="De ervaren collega die 'even weet hoe het zit'. AI maakt die kennis vindbaar en bruikbaar voor de rest van het team."
          />
          <Scenario
            Icon={Boxes}
            title="Bedrijfslogica zonder pakket"
            body="Voor jouw werkwijze bestaat geen kant-en-klaar systeem. Ik bouw precies wat je nodig hebt — niets meer, niets minder."
          />
        </div>
      </section>

      {/* Aanpak */}
      <section className="container-tight py-16">
        <h2 className="heading-rule text-2xl">Aanpak</h2>
        <ol className="grid gap-6 md:grid-cols-4">
          <Step n={1} title="AI-inspiratiesessie" body="Gratis, 45 minuten." />
          <Step n={2} title="Voorstel" body="Scope, planning en vaste prijs." />
          <Step n={3} title="Bouwen" body="" />
          <Step n={4} title="Oplevering" body="Met overdracht en korte training." />
        </ol>
      </section>

      {/* Cases preview */}
      <section className="container-tight py-16">
        <h2 className="heading-rule text-2xl">Cases</h2>
        <div className="grid gap-6 md:grid-cols-2">
          <CaseCard
            href="/cases/vision-scan-inventory"
            label="Live"
            title="Vision-scan inventory voor een wijn-magazijn"
            body="Producten herkennen via beeld, automatisch koppelen aan voorraad en orderlijst."
          />
          <CaseCard
            href="/cases"
            label="Lopend"
            title="Smaakprofielen voor een wijn-abonnement"
            body="Klantvoorkeuren omzetten in profielen voor schaalbaar persoonlijk advies."
          />
        </div>
      </section>

      {/* Footer CTA */}
      <section className="container-tight py-20">
        <div className="callout">
          <h3 className="text-2xl mb-2">Benieuwd wat AI in jouw bedrijf kan?</h3>
          <p className="text-ink/80 mb-6">
            45 minuten, gratis. Je krijgt een eerlijk beeld van wat haalbaar is — en wat niet.
          </p>
          <Link href="/contact" className="btn-primary">
            Plan een sessie <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </section>
    </>
  );
}

function Problem({
  Icon,
  title,
  body,
}: {
  Icon: React.ComponentType<{ className?: string }>;
  title: string;
  body: string;
}) {
  return (
    <div className="card-soft">
      <Icon className="h-8 w-8 text-brand-700 mb-3" />
      <h3 className="text-lg mb-2">{title}</h3>
      <p className="text-sm text-ink/75">{body}</p>
    </div>
  );
}

function Scenario({
  Icon,
  title,
  body,
}: {
  Icon: React.ComponentType<{ className?: string }>;
  title: string;
  body: string;
}) {
  return (
    <div className="callout">
      <Icon className="h-7 w-7 text-brand-700 mb-3" />
      <h3 className="text-lg mb-2">{title}</h3>
      <p className="text-sm text-ink/80">{body}</p>
    </div>
  );
}

function Step({ n, title, body }: { n: number; title: string; body: string }) {
  return (
    <li className="card-soft">
      <span className="text-sm font-semibold text-brand-700">Stap {n}</span>
      <h3 className="text-lg mt-1 mb-1">{title}</h3>
      {body && <p className="text-sm text-ink/75">{body}</p>}
    </li>
  );
}

function CaseCard({
  href,
  label,
  title,
  body,
}: {
  href: string;
  label: string;
  title: string;
  body: string;
}) {
  return (
    <Link href={href} className="card-soft block hover:border-brand-200 transition">
      <span className="inline-block text-xs font-medium text-brand-700 bg-brand-50 px-2 py-0.5 rounded-full mb-3">
        {label}
      </span>
      <h3 className="text-xl mb-2">{title}</h3>
      <p className="text-sm text-ink/75">{body}</p>
      <span className="inline-flex items-center gap-1 mt-4 text-sm text-brand-700">
        Lees meer <ArrowRight className="h-4 w-4" />
      </span>
    </Link>
  );
}
