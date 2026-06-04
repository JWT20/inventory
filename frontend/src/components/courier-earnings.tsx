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

interface EarningsCustomer {
  customer_name: string;
  boxes: number;
  charge_amount: number;
}

interface Earnings {
  month: string;
  charge_cents: number;
  platform_cents: number;
  courier_cents: number;
  total_boxes: number;
  total_charge: number;
  total_platform: number;
  total_courier: number;
  customers: EarningsCustomer[];
}

const MONTH_NAMES = [
  "januari", "februari", "maart", "april", "mei", "juni",
  "juli", "augustus", "september", "oktober", "november", "december",
];

function currentMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return `${MONTH_NAMES[m - 1]} ${y}`;
}

function euro(v: number): string {
  return `€ ${v.toFixed(2)}`;
}

function cents(v: number): string {
  return `€ ${(v / 100).toFixed(2)}`;
}

export function CourierEarningsPage() {
  const [month, setMonth] = useState(() => currentMonth());
  const [data, setData] = useState<Earnings | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.courierEarnings(month);
      setData(result);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Kan facturatie niet laden");
    } finally {
      setLoading(false);
    }
  }, [month]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Facturatie</h2>
      </div>

      <div className="flex items-center gap-2 mb-4">
        <Button variant="outline" size="sm" onClick={() => setMonth((m) => shiftMonth(m, -1))}>
          &larr;
        </Button>
        <span className="text-sm font-medium min-w-[9rem] text-center capitalize">
          {monthLabel(month)}
        </span>
        <Button variant="outline" size="sm" onClick={() => setMonth((m) => shiftMonth(m, 1))}>
          &rarr;
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setMonth(currentMonth())}
          className="ml-2"
        >
          Deze maand
        </Button>
      </div>

      {loading ? (
        <Skeleton className="h-40 w-full" />
      ) : !data ? null : (
        <>
          <Card className="p-4 mb-4">
            <p className="text-sm text-muted-foreground">
              Te factureren ({data.total_boxes} dozen &times; {cents(data.charge_cents)})
            </p>
            <p className="text-3xl font-bold">{euro(data.total_charge)}</p>
          </Card>

          {data.customers.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Geen gescande dozen in {monthLabel(month)}.
            </p>
          ) : (
            <Card className="p-0 overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Klant</TableHead>
                    <TableHead className="text-right">Dozen</TableHead>
                    <TableHead className="text-right">Te factureren</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.customers.map((c) => (
                    <TableRow key={c.customer_name}>
                      <TableCell className="font-medium">{c.customer_name}</TableCell>
                      <TableCell className="text-right">{c.boxes}</TableCell>
                      <TableCell className="text-right">{euro(c.charge_amount)}</TableCell>
                    </TableRow>
                  ))}
                  <TableRow className="font-bold border-t-2">
                    <TableCell>Totaal</TableCell>
                    <TableCell className="text-right">{data.total_boxes}</TableCell>
                    <TableCell className="text-right">{euro(data.total_charge)}</TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </Card>
          )}
        </>
      )}
    </>
  );
}
