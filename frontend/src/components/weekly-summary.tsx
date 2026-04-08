import { useState, useEffect, useCallback } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface CustomerOrder {
  customer_name: string;
  quantity: number;
  effective_price: number | null;
  line_total: number | null;
  remarks: string;
}

interface Wine {
  sku_id: number;
  sku_code: string;
  sku_name: string;
  default_price: number | null;
  total_quantity: number;
  orders: CustomerOrder[];
  wine_total: number | null;
}

interface SupplierGroup {
  supplier_id: number | null;
  supplier_name: string;
  wines: Wine[];
  supplier_total_quantity: number;
  supplier_total_value: number | null;
}

interface WeeklySummary {
  week: string;
  deadline: string;
  suppliers: SupplierGroup[];
  grand_total_quantity: number;
  grand_total_value: number | null;
}

function getISOWeek(date: Date): string {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7));
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${d.getUTCFullYear()}-W${String(weekNo).padStart(2, "0")}`;
}

function shiftWeek(week: string, delta: number): string {
  const [, y, w] = week.match(/^(\d{4})-W(\d{2})$/) || [];
  if (!y) return week;
  // Parse monday of current week, then shift by delta weeks
  const monday = new Date(`${y}-01-04`); // Jan 4 is always in week 1
  const dayOfWeek = monday.getDay() || 7;
  monday.setDate(monday.getDate() - dayOfWeek + 1 + (Number(w) - 1) * 7 + delta * 7);
  return getISOWeek(monday);
}

function formatPrice(v: number | null): string {
  if (v === null) return "-";
  return `\u20AC ${v.toFixed(2)}`;
}

export function WeeklySummaryPage() {
  const [week, setWeek] = useState(() => getISOWeek(new Date()));
  const [data, setData] = useState<WeeklySummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.weeklyOrderSummary(week);
      setData(result);
      setCollapsed({});
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Kan overzicht niet laden");
    } finally {
      setLoading(false);
    }
  }, [week]);

  useEffect(() => {
    load();
  }, [load]);

  const toggleCollapse = (key: string) => {
    setCollapsed((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  return (
    <>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Weekoverzicht</h2>
      </div>

      <div className="flex items-center gap-2 mb-4">
        <Button variant="outline" size="sm" onClick={() => setWeek((w) => shiftWeek(w, -1))}>
          &larr;
        </Button>
        <span className="text-sm font-medium min-w-[7rem] text-center">{week}</span>
        <Button variant="outline" size="sm" onClick={() => setWeek((w) => shiftWeek(w, 1))}>
          &rarr;
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setWeek(getISOWeek(new Date()))}
          className="ml-2"
        >
          Vandaag
        </Button>
      </div>

      {loading && (
        <div className="space-y-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i} className="p-4">
              <div className="flex justify-between items-center">
                <Skeleton className="h-5 w-40" />
                <Skeleton className="h-5 w-24" />
              </div>
              <div className="mt-3 space-y-2">
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-full" />
              </div>
            </Card>
          ))}
        </div>
      )}

      {!loading && data && data.suppliers.length === 0 && (
        <p className="text-center text-muted-foreground py-10">
          Geen bestellingen in deze week
        </p>
      )}

      {!loading && data && data.suppliers.length > 0 && (
        <div className="space-y-4">
          {data.suppliers.map((supplier) => {
            const key = String(supplier.supplier_id ?? "none");
            const isCollapsed = collapsed[key];
            return (
              <Card key={key} className="overflow-hidden">
                <button
                  className="w-full px-4 py-3 flex justify-between items-center text-left hover:bg-muted/50 transition-colors"
                  onClick={() => toggleCollapse(key)}
                >
                  <div>
                    <span className="font-semibold">{supplier.supplier_name}</span>
                    <span className="ml-2 text-sm text-muted-foreground">
                      {supplier.supplier_total_quantity} dozen
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    {supplier.supplier_total_value != null && (
                      <span className="text-sm font-medium">
                        {formatPrice(supplier.supplier_total_value)}
                      </span>
                    )}
                    <span className="text-muted-foreground text-xs">
                      {isCollapsed ? "\u25B6" : "\u25BC"}
                    </span>
                  </div>
                </button>

                {!isCollapsed && (
                  <div className="border-t border-border">
                    {supplier.wines.map((wine) => (
                      <div key={wine.sku_id} className="border-b border-border last:border-b-0">
                        <div className="px-4 py-2 bg-muted/30 flex justify-between items-center">
                          <div>
                            <span className="text-sm font-medium">{wine.sku_name}</span>
                            <span className="ml-2 text-xs text-muted-foreground">
                              {wine.sku_code}
                            </span>
                          </div>
                          <div className="text-right">
                            <span className="text-sm">{wine.total_quantity} dozen</span>
                            {wine.wine_total != null && (
                              <span className="ml-3 text-sm font-medium">
                                {formatPrice(wine.wine_total)}
                              </span>
                            )}
                          </div>
                        </div>
                        <Table>
                          <TableHeader>
                            <TableRow className="border-border/50">
                              <TableHead className="h-8 text-xs">Klant</TableHead>
                              <TableHead className="h-8 text-xs text-right">Aantal dozen</TableHead>
                              <TableHead className="h-8 text-xs text-right">Prijs</TableHead>
                              <TableHead className="h-8 text-xs text-right">Totaal</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {wine.orders.map((order, i) => (
                              <TableRow key={i} className="border-border/50">
                                <TableCell className="py-1.5">
                                  {order.customer_name}
                                  {order.remarks && (
                                    <p className="text-xs text-muted-foreground italic mt-0.5">{order.remarks}</p>
                                  )}
                                </TableCell>
                                <TableCell className="py-1.5 text-right">{order.quantity}x</TableCell>
                                <TableCell className="py-1.5 text-right text-muted-foreground">
                                  {formatPrice(order.effective_price)}
                                </TableCell>
                                <TableCell className="py-1.5 text-right font-medium">
                                  {formatPrice(order.line_total)}
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </div>
                    ))}
                  </div>
                )}
              </Card>
            );
          })}

          <Card className="p-4">
            <div className="flex justify-between items-center font-semibold">
              <span>Totaal</span>
              <div className="flex items-center gap-4">
                <span>{data.grand_total_quantity} dozen</span>
                {data.grand_total_value != null && (
                  <span>{formatPrice(data.grand_total_value)}</span>
                )}
              </div>
            </div>
          </Card>

          {data.deadline && (
            <p className="text-xs text-muted-foreground text-center">
              Deadline: zondag {data.deadline}
            </p>
          )}
        </div>
      )}
    </>
  );
}
