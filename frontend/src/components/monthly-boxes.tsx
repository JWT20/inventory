import { useState, useEffect, useCallback } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatBoxesBottles } from "@/lib/units";

interface Organization {
  id: number;
  name: string;
}

interface MonthRow {
  month: string; // "YYYY-MM"
  boxes: number;
  bottles: number;
  items: number;
  item_order_count: number;
  item_line_count: number;
}

interface OrgReport {
  organization_id: number | null;
  organization_name: string;
  total_boxes: number;
  total_bottles: number;
  total_items: number;
  total_item_orders: number;
  total_item_lines: number;
  months: MonthRow[];
}

interface MonthlyBoxesResponse {
  organizations: OrgReport[];
  replenishment: OrgReport[];
}

// Customer orders left the building; replenishment moved the merchant's own
// stock onto a shelf. Both are work the courier did, but adding them up would
// count the same bottles twice — once when the box is put on the shelf and
// again on the customer order that ships them. Hence two tabs, never one total.
type ReportTab = "customer" | "replenishment";

const NL_MONTHS = [
  "januari",
  "februari",
  "maart",
  "april",
  "mei",
  "juni",
  "juli",
  "augustus",
  "september",
  "oktober",
  "november",
  "december",
];

function formatMonth(month: string): string {
  const [year, mm] = month.split("-");
  const idx = parseInt(mm, 10) - 1;
  return `${NL_MONTHS[idx] ?? mm} ${year}`;
}

function currentMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

// A month is "closed" (final, ready to invoice) once it lies before the current
// calendar month. A finalized order never moves to an earlier month afterwards,
// so a past month's total can no longer change.
function isClosedMonth(month: string): boolean {
  return month < currentMonth();
}

export function MonthlyBoxesPage() {
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [selectedOrganizationId, setSelectedOrganizationId] = useState("");
  const [report, setReport] = useState<OrgReport | null>(null);
  const [replenishment, setReplenishment] = useState<OrgReport | null>(null);
  const [tab, setTab] = useState<ReportTab>("customer");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api
      .listOrganizations()
      .then((orgs: Organization[]) => setOrganizations(orgs))
      .catch(() => toast.error("Kan handelaren niet laden"));
  }, []);

  const load = useCallback(async () => {
    if (!selectedOrganizationId) {
      setReport(null);
      setReplenishment(null);
      return;
    }
    try {
      setLoading(true);
      const resp: MonthlyBoxesResponse = await api.monthlyBookedBoxes(
        selectedOrganizationId,
      );
      setReport(resp.organizations[0] ?? null);
      setReplenishment(resp.replenishment[0] ?? null);
    } catch {
      toast.error("Kan overzicht niet laden");
    } finally {
      setLoading(false);
    }
  }, [selectedOrganizationId]);

  useEffect(() => {
    load();
  }, [load]);

  const shown = tab === "replenishment" ? replenishment : report;
  // Decided per merchant, not per month row: the table has one set of headers,
  // so a month without barcode orders keeps the columns and shows 0.
  const showItemCounts = (shown?.total_item_lines ?? 0) > 0;

  return (
    <>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Verwerkt per maand</h2>
      </div>

      <p className="text-sm text-muted-foreground mb-4">
        Verwerkte hoeveelheden voor voltooide en gesloten orders, per maand waarin
        de order is afgerond. Voor barcode-producten tellen we ook het aantal orders
        en orderregels.
      </p>

      <div className="mb-4">
        <Select
          value={selectedOrganizationId}
          onValueChange={setSelectedOrganizationId}
        >
          <SelectTrigger>
            <SelectValue placeholder="Kies handelaar" />
          </SelectTrigger>
          <SelectContent>
            {organizations.map((org) => (
              <SelectItem key={org.id} value={String(org.id)}>
                {org.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {selectedOrganizationId && (
        <div className="inline-flex rounded-md border border-border overflow-hidden mb-4 text-sm">
          <button
            type="button"
            onClick={() => setTab("customer")}
            className={`px-3 py-1.5 transition-colors ${
              tab === "customer"
                ? "bg-primary text-primary-foreground"
                : "bg-background hover:bg-muted"
            }`}
          >
            Klantorders
          </button>
          <button
            type="button"
            onClick={() => setTab("replenishment")}
            className={`px-3 py-1.5 transition-colors border-l border-border ${
              tab === "replenishment"
                ? "bg-primary text-primary-foreground"
                : "bg-background hover:bg-muted"
            }`}
          >
            Bevoorrading
          </button>
        </div>
      )}

      {!selectedOrganizationId ? (
        <p className="text-center text-muted-foreground py-10">
          Kies een handelaar om het overzicht te bekijken
        </p>
      ) : loading ? (
        <Card className="p-4 space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </Card>
      ) : !shown || shown.months.length === 0 ? (
        <p className="text-center text-muted-foreground py-10">
          {tab === "replenishment"
            ? "Nog geen bevoorrading gepickt voor deze handelaar"
            : "Nog geen boekingen voor deze handelaar"}
        </p>
      ) : (
        <Card className="p-0 overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Maand</TableHead>
                {showItemCounts && (
                  <>
                    <TableHead className="text-right">Orders</TableHead>
                    <TableHead className="text-right">Regels</TableHead>
                  </>
                )}
                <TableHead className="text-right">Geboekt</TableHead>
                <TableHead className="text-right">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.months.map((m) => {
                const closed = isClosedMonth(m.month);
                return (
                  <TableRow key={m.month}>
                    <TableCell>{formatMonth(m.month)}</TableCell>
                    {showItemCounts && (
                      <>
                        <TableCell className="text-right tabular-nums">
                          {m.item_order_count}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {m.item_line_count}
                        </TableCell>
                      </>
                    )}
                    <TableCell className="text-right tabular-nums">
                      {formatBoxesBottles(m.boxes, m.bottles, m.items)}
                    </TableCell>
                    <TableCell className="text-right">
                      <Badge variant={closed ? "completed" : "pending"}>
                        {closed ? "Afgesloten" : "Lopend"}
                      </Badge>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
          {tab === "replenishment" && (
            <p className="px-4 py-2 text-xs text-muted-foreground border-t border-border">
              Dozen die uit het magazijn naar de winkel- of webshopplank zijn
              gepickt. Deze staan los van de klantorders: de flessen verlaten het
              pand pas op de order die ze later verscheept.
            </p>
          )}
        </Card>
      )}
    </>
  );
}
