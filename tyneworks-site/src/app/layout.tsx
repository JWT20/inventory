import type { Metadata } from "next";
import "./globals.css";
import { SiteHeader } from "@/components/site-header";
import { SiteFooter } from "@/components/site-footer";

export const metadata: Metadata = {
  title: "Tyne Works — Maatwerk AI-automatisering voor MKB",
  description:
    "Tyne Works helpt MKB met maatwerk AI-automatisering op de plekken waar generieke systemen tekortschieten: kennis in hoofden, Excel rond het ERP, bedrijfsspecifieke logica.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="nl">
      <body className="min-h-screen flex flex-col">
        <SiteHeader />
        <main className="flex-1">{children}</main>
        <SiteFooter />
      </body>
    </html>
  );
}
