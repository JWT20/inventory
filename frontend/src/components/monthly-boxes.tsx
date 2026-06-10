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
}

interface OrgReport {
  organization_id: number | null;
  organization_name: string;
  total_boxes: number;
  total_bottles: number;
  months: MonthRow[];
}

interface MonthlyBoxesResponse {
  organizations: OrgReport[];
}

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
      return;
    }
    try {
      setLoading(true);
      const resp: MonthlyBoxesResponse = await api.monthlyBookedBoxes(
        selectedOrganizationId,
      );
      setReport(resp.organizations[0] ?? null);
    } catch {
      toast.error("Kan overzicht niet laden");
    } finally {
      setLoading(false);
    }
  }, [selectedOrganizationId]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Geboekt per maand</h2>
      </div>

      <p className="text-sm text-muted-foreground mb-4">
        Aantal geboekte dozen en flessen voor voltooide en gesloten orders,
        per maand waarin de order is afgerond.
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
      ) : !report || report.months.length === 0 ? (
        <p className="text-center text-muted-foreground py-10">
          Nog geen boekingen voor deze handelaar
        </p>
      ) : (
        <Card className="p-0 overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Maand</TableHead>
                <TableHead className="text-right">Geboekt</TableHead>
                <TableHead className="text-right">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {report.months.map((m) => {
                const closed = isClosedMonth(m.month);
                return (
                  <TableRow key={m.month}>
                    <TableCell>{formatMonth(m.month)}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatBoxesBottles(m.boxes, m.bottles)}
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
        </Card>
      )}
    </>
  );
}
