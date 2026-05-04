import fs from "node:fs";
import path from "node:path";
import matter from "gray-matter";

const CASES_DIR = path.join(process.cwd(), "src/content/cases");

export interface CaseMeta {
  slug: string;
  title: string;
  label: string;
  summary: string;
  client?: string;
  date?: string;
}

export function getCaseSlugs(): string[] {
  if (!fs.existsSync(CASES_DIR)) return [];
  return fs
    .readdirSync(CASES_DIR)
    .filter((f) => f.endsWith(".mdx"))
    .map((f) => f.replace(/\.mdx$/, ""));
}

export function getCaseMeta(slug: string): CaseMeta {
  const filePath = path.join(CASES_DIR, `${slug}.mdx`);
  const raw = fs.readFileSync(filePath, "utf8");
  const { data } = matter(raw);
  return {
    slug,
    title: data.title ?? slug,
    label: data.label ?? "",
    summary: data.summary ?? "",
    client: data.client,
    date: data.date,
  };
}

export function getAllCases(): CaseMeta[] {
  return getCaseSlugs()
    .map(getCaseMeta)
    .sort((a, b) => (a.date && b.date ? b.date.localeCompare(a.date) : 0));
}
