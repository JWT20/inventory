# Tyne Works — site

Marketingsite voor Tyne Works (Jans Tigelaar). Maatwerk AI-automatisering voor MKB.

## Stack

- Next.js 15 (App Router) — statische export (`output: "export"`)
- Tailwind CSS
- MDX voor cases (frontmatter via gray-matter)
- Lucide-react voor iconen
- Te hosten op Vercel of elke statische host

## Lokaal draaien

```bash
npm install
npm run dev
```

Open http://localhost:3000.

## Bouwen

```bash
npm run build
```

Output staat in `out/` (statische bestanden, klaar om te uploaden).

## Structuur

```
src/
  app/
    page.tsx            home
    aanpak/page.tsx
    cases/page.tsx
    cases/[slug]/page.tsx
    over/page.tsx
    contact/page.tsx
    layout.tsx
    globals.css
  components/
    site-header.tsx
    site-footer.tsx
  content/
    cases/
      vision-scan-inventory.mdx   ← cases als MDX-bestanden
  lib/
    cases.ts            ← leest MDX-frontmatter
  mdx-components.tsx
```

## Cases toevoegen

Plaats een `.mdx` bestand in `src/content/cases/` met frontmatter:

```mdx
---
title: "Titel van de case"
label: "Live"           # of "Lopend"
summary: "Korte één-regel-samenvatting."
date: "2025-09-01"
---

## Het probleem
...
```

De case verschijnt automatisch op `/cases` en op `/cases/<bestandsnaam>`.

## Brand

- Primair: deep blue `#1e3a8a` (`brand-700`)
- Accent-tint: `#eef3ff` (`brand-50`)
- Tekst: ink `#0b1530`
- Afgeronde hoeken (`rounded-2xl`), subtiele schaduw

## Volgende stappen

- [ ] Foto Jans op Over-pagina
- [ ] Calendly-link op Contact-pagina
- [ ] Tweede case toevoegen (smaakprofielen wijn-abonnement)
- [ ] Domein `tyneworks.nl` registreren en koppelen
- [ ] Plausible analytics
- [ ] Resend/Formspree voor contactformulier (eventueel)
