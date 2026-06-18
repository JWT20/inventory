/* Week helpers (same logic as weekly-summary.tsx) */

export function getISOWeek(date: Date): string {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7));
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${d.getUTCFullYear()}-W${String(weekNo).padStart(2, "0")}`;
}

export function shiftWeek(week: string, delta: number): string {
  const [, y, w] = week.match(/^(\d{4})-W(\d{2})$/) || [];
  if (!y) return week;
  const monday = new Date(`${y}-01-04`);
  const dayOfWeek = monday.getDay() || 7;
  monday.setDate(monday.getDate() - dayOfWeek + 1 + (Number(w) - 1) * 7 + delta * 7);
  return getISOWeek(monday);
}
