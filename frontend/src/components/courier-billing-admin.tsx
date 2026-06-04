import { useState, useEffect, useCallback } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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

const ANY = "__any__";
const SERVICE_LABELS: Record<string, string> = {
  wine_pick: "Wijn (per doos)",
  book_pick: "Boeken (per stuk)",
  general_pick: "Overig (per stuk)",
};

interface RateCard {
  id: number;
  courier_id: number | null;
  customer_id: number | null;
  organization_id: number | null;
  service_type: string | null;
  unit_type: string;
  charge_cents: number;
  platform_cents: number;
  courier_cents: number;
  effective_from: string;
  effective_until: string | null;
  courier_name: string | null;
  customer_name: string | null;
  organization_name: string | null;
}

interface Settlement {
  id: number;
  courier_id: number;
  courier_name: string | null;
  period_month: string;
  status: string;
  total_units: number;
  total_charge_cents: number;
  total_platform_cents: number;
  total_courier_cents: number;
}

interface NamedUser { id: number; username: string; role: string }
interface Named { id: number; name: string }

const euro = (cents: number) => `€ ${(cents / 100).toFixed(2)}`;

export function CourierBillingAdminPage() {
  const [couriers, setCouriers] = useState<NamedUser[]>([]);
  const [customers, setCustomers] = useState<Named[]>([]);
  const [orgs, setOrgs] = useState<Named[]>([]);

  const loadRefs = useCallback(async () => {
    try {
      const [users, custs, organizations] = await Promise.all([
        api.listUsers(),
        api.listCustomers(),
        api.listOrganizations(),
      ]);
      setCouriers((users as NamedUser[]).filter((u) => u.role === "courier"));
      setCustomers(custs as Named[]);
      setOrgs(organizations as Named[]);
    } catch {
      /* refs are optional for display */
    }
  }, []);

  useEffect(() => {
    loadRefs();
  }, [loadRefs]);

  return (
    <div className="space-y-8">
      <RateCardsSection couriers={couriers} customers={customers} orgs={orgs} />
      <SettlementsSection couriers={couriers} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Rate cards
// ---------------------------------------------------------------------------
function RateCardsSection({ couriers, customers, orgs }: {
  couriers: NamedUser[]; customers: Named[]; orgs: Named[];
}) {
  const [cards, setCards] = useState<RateCard[]>([]);
  const [showForm, setShowForm] = useState(false);

  const load = useCallback(async () => {
    try {
      setCards(await api.listRateCards());
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Kan tariefkaarten niet laden");
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const remove = async (id: number) => {
    if (!confirm("Tariefkaart verwijderen?")) return;
    try {
      await api.deleteRateCard(id);
      toast.success("Verwijderd");
      load();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Verwijderen mislukt");
    }
  };

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Tariefkaarten</h2>
        <Button size="sm" onClick={() => setShowForm(true)}>Nieuwe tariefkaart</Button>
      </div>

      <Card className="p-0 overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Koerier</TableHead>
              <TableHead>Klant</TableHead>
              <TableHead>Organisatie</TableHead>
              <TableHead>Dienst</TableHead>
              <TableHead className="text-right">Klant</TableHead>
              <TableHead className="text-right">Platform</TableHead>
              <TableHead className="text-right">Koerier</TableHead>
              <TableHead>Geldig</TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {cards.length === 0 ? (
              <TableRow>
                <TableCell colSpan={9} className="text-center text-muted-foreground py-6">
                  Nog geen tariefkaarten.
                </TableCell>
              </TableRow>
            ) : cards.map((c) => (
              <TableRow key={c.id}>
                <TableCell>{c.courier_name ?? <Muted>alle</Muted>}</TableCell>
                <TableCell>{c.customer_name ?? <Muted>alle</Muted>}</TableCell>
                <TableCell>{c.organization_name ?? <Muted>alle</Muted>}</TableCell>
                <TableCell>{c.service_type ? (SERVICE_LABELS[c.service_type] ?? c.service_type) : <Muted>alle</Muted>}</TableCell>
                <TableCell className="text-right font-medium">{euro(c.charge_cents)}</TableCell>
                <TableCell className="text-right">{euro(c.platform_cents)}</TableCell>
                <TableCell className="text-right">{euro(c.courier_cents)}</TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {c.effective_from} → {c.effective_until ?? "∞"}
                </TableCell>
                <TableCell>
                  <Button variant="ghost" size="sm" onClick={() => remove(c.id)}>×</Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>

      {showForm && (
        <RateCardDialog
          couriers={couriers} customers={customers} orgs={orgs}
          onClose={() => setShowForm(false)}
          onSaved={() => { setShowForm(false); load(); }}
        />
      )}
    </div>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return <span className="text-muted-foreground italic">{children}</span>;
}

function RateCardDialog({ couriers, customers, orgs, onClose, onSaved }: {
  couriers: NamedUser[]; customers: Named[]; orgs: Named[];
  onClose: () => void; onSaved: () => void;
}) {
  const [courierId, setCourierId] = useState(ANY);
  const [customerId, setCustomerId] = useState(ANY);
  const [orgId, setOrgId] = useState(ANY);
  const [service, setService] = useState(ANY);
  const [unit, setUnit] = useState("box");
  const [charge, setCharge] = useState("0.50");
  const [platform, setPlatform] = useState("0.17");
  const [courier, setCourier] = useState("0.33");
  const [from, setFrom] = useState(() => new Date().toISOString().slice(0, 10));
  const [until, setUntil] = useState("");
  const [saving, setSaving] = useState(false);

  const toCents = (v: string) => Math.round(parseFloat(v || "0") * 100);
  const chargeC = toCents(charge), platC = toCents(platform), courC = toCents(courier);
  const splitOk = chargeC === platC + courC;

  const submit = async () => {
    if (!splitOk) { toast.error("Klant = platform + koerier moet kloppen"); return; }
    setSaving(true);
    try {
      await api.createRateCard({
        courier_id: courierId === ANY ? null : Number(courierId),
        customer_id: customerId === ANY ? null : Number(customerId),
        organization_id: orgId === ANY ? null : Number(orgId),
        service_type: service === ANY ? null : service,
        unit_type: unit,
        charge_cents: chargeC,
        platform_cents: platC,
        courier_cents: courC,
        effective_from: from,
        effective_until: until || null,
      });
      toast.success("Tariefkaart aangemaakt");
      onSaved();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Opslaan mislukt");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader><DialogTitle>Nieuwe tariefkaart</DialogTitle></DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Koerier">
            <PickSelect value={courierId} onChange={setCourierId}
              options={couriers.map((c) => ({ value: String(c.id), label: c.username }))} />
          </Field>
          <Field label="Klant">
            <PickSelect value={customerId} onChange={setCustomerId}
              options={customers.map((c) => ({ value: String(c.id), label: c.name }))} />
          </Field>
          <Field label="Organisatie">
            <PickSelect value={orgId} onChange={setOrgId}
              options={orgs.map((o) => ({ value: String(o.id), label: o.name }))} />
          </Field>
          <Field label="Dienst">
            <PickSelect value={service} onChange={setService}
              options={Object.entries(SERVICE_LABELS).map(([v, l]) => ({ value: v, label: l }))} />
          </Field>
          <Field label="Eenheid">
            <Select value={unit} onValueChange={setUnit}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="box">Doos</SelectItem>
                <SelectItem value="item">Stuk</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <div />
          <Field label="Klant betaalt (€)">
            <Input type="number" step="0.01" value={charge} onChange={(e) => setCharge(e.target.value)} />
          </Field>
          <div />
          <Field label="Platform (€)">
            <Input type="number" step="0.01" value={platform} onChange={(e) => setPlatform(e.target.value)} />
          </Field>
          <Field label="Koerier (€)">
            <Input type="number" step="0.01" value={courier} onChange={(e) => setCourier(e.target.value)} />
          </Field>
          <Field label="Geldig vanaf">
            <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </Field>
          <Field label="Geldig tot (optioneel)">
            <Input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
          </Field>
        </div>
        {!splitOk && (
          <p className="text-sm text-destructive">
            Klant (€{charge}) moet gelijk zijn aan platform + koerier (€{(platC + courC) / 100}).
          </p>
        )}
        <div className="flex justify-end gap-2 mt-2">
          <Button variant="outline" onClick={onClose}>Annuleren</Button>
          <Button onClick={submit} disabled={saving || !splitOk}>Opslaan</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      {children}
    </div>
  );
}

function PickSelect({ value, onChange, options }: {
  value: string; onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger><SelectValue /></SelectTrigger>
      <SelectContent>
        <SelectItem value={ANY}>alle</SelectItem>
        {options.map((o) => (
          <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

// ---------------------------------------------------------------------------
// Settlements
// ---------------------------------------------------------------------------
function SettlementsSection({ couriers }: { couriers: NamedUser[] }) {
  const [settlements, setSettlements] = useState<Settlement[]>([]);
  const [courierId, setCourierId] = useState("");
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setSettlements(await api.listSettlements());
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Kan afrekeningen niet laden");
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const create = async () => {
    if (!courierId) { toast.error("Kies een koerier"); return; }
    setBusy(true);
    try {
      await api.createSettlement(Number(courierId), month);
      toast.success("Afrekening aangemaakt");
      load();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Aanmaken mislukt";
      // Some units have no tariff — offer to settle them at €0 explicitly.
      if (msg.includes("Geen tarief") && confirm(`${msg}\n\nToch afrekenen (deze stuks op € 0)?`)) {
        try {
          await api.createSettlement(Number(courierId), month, true);
          toast.success("Afrekening aangemaakt (incl. ongetarifeerde stuks)");
          load();
        } catch (e: unknown) {
          toast.error(e instanceof Error ? e.message : "Aanmaken mislukt");
        }
      } else {
        toast.error(msg);
      }
    } finally {
      setBusy(false);
    }
  };

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); toast.success(ok); load(); }
    catch (err: unknown) { toast.error(err instanceof Error ? err.message : "Mislukt"); }
  };

  const statusBadge = (s: string) =>
    s === "paid" ? <Badge>betaald</Badge>
    : s === "approved" ? <Badge variant="secondary">goedgekeurd</Badge>
    : <Badge variant="outline">concept</Badge>;

  return (
    <div>
      <h2 className="text-xl font-bold mb-4">Afrekeningen</h2>

      <Card className="p-4 mb-4 flex flex-wrap items-end gap-3">
        <Field label="Koerier">
          <Select value={courierId} onValueChange={setCourierId}>
            <SelectTrigger className="w-40"><SelectValue placeholder="Kies koerier" /></SelectTrigger>
            <SelectContent>
              {couriers.map((c) => (
                <SelectItem key={c.id} value={String(c.id)}>{c.username}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field label="Maand">
          <Input type="month" value={month} onChange={(e) => setMonth(e.target.value)} className="w-40" />
        </Field>
        <Button onClick={create} disabled={busy}>Afrekening aanmaken</Button>
      </Card>

      <Card className="p-0 overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Koerier</TableHead>
              <TableHead>Maand</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Stuks</TableHead>
              <TableHead className="text-right">Omzet (totaal)</TableHead>
              <TableHead className="text-right">Jouw deel</TableHead>
              <TableHead className="text-right">Koerier deel</TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {settlements.length === 0 ? (
              <TableRow>
                <TableCell colSpan={8} className="text-center text-muted-foreground py-6">
                  Nog geen afrekeningen.
                </TableCell>
              </TableRow>
            ) : settlements.map((s) => (
              <TableRow key={s.id}>
                <TableCell>{s.courier_name ?? s.courier_id}</TableCell>
                <TableCell>{s.period_month}</TableCell>
                <TableCell>{statusBadge(s.status)}</TableCell>
                <TableCell className="text-right">{s.total_units}</TableCell>
                <TableCell className="text-right">{euro(s.total_charge_cents)}</TableCell>
                <TableCell className="text-right font-medium">{euro(s.total_platform_cents)}</TableCell>
                <TableCell className="text-right">{euro(s.total_courier_cents)}</TableCell>
                <TableCell className="text-right space-x-1 whitespace-nowrap">
                  {s.status === "draft" && (
                    <>
                      <Button variant="outline" size="sm"
                        onClick={() => act(() => api.approveSettlement(s.id), "Goedgekeurd")}>
                        Goedkeuren
                      </Button>
                      <Button variant="ghost" size="sm"
                        onClick={() => act(() => api.deleteSettlement(s.id), "Verwijderd")}>×</Button>
                    </>
                  )}
                  {s.status === "approved" && (
                    <Button variant="outline" size="sm"
                      onClick={() => act(() => api.paySettlement(s.id), "Betaald")}>
                      Markeer betaald
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
