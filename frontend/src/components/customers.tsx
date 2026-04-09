import { useState, useEffect, useCallback } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ArrowLeft, Plus, Trash2, Search } from "lucide-react";

// ── Types ────────────────────────────────────────────────────────────

interface Customer {
  id: number;
  name: string;
  show_prices: boolean;
  discount_percentage: number | null;
  delivery_day: string;
  sku_ids: number[];
  sku_count: number;
  created_at: string;
}

const DELIVERY_DAY_LABELS: Record<string, string> = {
  wednesday: "Woensdag",
  thursday: "Donderdag",
  friday: "Vrijdag",
};

interface CustomerSKU {
  sku_id: number;
  sku_code: string;
  sku_name: string;
  default_price: number | null;
  unit_price: number | null;
  discount_type: string | null;
  discount_value: number | null;
  effective_price: number | null;
}

interface SKU {
  id: number;
  sku_code: string;
  name: string;
}

// ── Main page component ──────────────────────────────────────────────

export function CustomersPage() {
  const [selectedCustomerId, setSelectedCustomerId] = useState<number | null>(null);

  if (selectedCustomerId !== null) {
    return (
      <CustomerDetail
        customerId={selectedCustomerId}
        onBack={() => setSelectedCustomerId(null)}
      />
    );
  }

  return <CustomerList onSelect={(id) => setSelectedCustomerId(id)} />;
}

// ── Customer list ────────────────────────────────────────────────────

function CustomerList({ onSelect }: { onSelect: (id: number) => void }) {
  const { user } = useAuth();
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);

  const load = useCallback(async () => {
    try {
      setCustomers(await api.listCustomers());
    } catch {
      toast.error("Fout bij laden klanten");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const isAdmin = user?.is_platform_admin;

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Klanten</h2>
        <Button size="sm" onClick={() => setShowNew(true)}>
          <Plus className="h-4 w-4 mr-1" />
          Klant
        </Button>
      </div>

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <Card key={i} className="p-4 animate-pulse h-16" />
          ))}
        </div>
      ) : customers.length === 0 ? (
        <p className="text-center text-muted-foreground py-8">Geen klanten</p>
      ) : (
        <div className="border rounded-md">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Naam</TableHead>
                <TableHead className="w-[120px]">Leverdag</TableHead>
                <TableHead className="w-[100px] text-right">Korting</TableHead>
                <TableHead className="w-[100px] text-right">Producten</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {customers.map((c) => (
                <TableRow
                  key={c.id}
                  className="cursor-pointer hover:bg-muted/50"
                  onClick={() => onSelect(c.id)}
                >
                  <TableCell className="font-medium">{c.name}</TableCell>
                  <TableCell>
                    <Badge variant="secondary">
                      {DELIVERY_DAY_LABELS[c.delivery_day] ?? c.delivery_day}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">
                    {c.discount_percentage != null
                      ? `${c.discount_percentage}%`
                      : <span className="text-muted-foreground">-</span>}
                  </TableCell>
                  <TableCell className="text-right">{c.sku_count}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <NewCustomerDialog
        open={showNew}
        onClose={() => setShowNew(false)}
        onCreated={() => { setShowNew(false); load(); }}
        isAdmin={!!isAdmin}
      />
    </div>
  );
}

// ── Customer detail ──────────────────────────────────────────────────

function CustomerDetail({
  customerId,
  onBack,
}: {
  customerId: number;
  onBack: () => void;
}) {
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [skus, setSKUs] = useState<CustomerSKU[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // Editable fields
  const [name, setName] = useState("");
  const [showPrices, setShowPrices] = useState(true);
  const [discountPct, setDiscountPct] = useState("");
  const [deliveryDay, setDeliveryDay] = useState("thursday");
  const [dirty, setDirty] = useState(false);

  // Add product dialog
  const [showAddProduct, setShowAddProduct] = useState(false);

  const load = useCallback(async () => {
    try {
      const [c, s] = await Promise.all([
        api.getCustomer(customerId),
        api.listCustomerSKUs(customerId),
      ]);
      setCustomer(c);
      setSKUs(s);
      setName(c.name);
      setShowPrices(c.show_prices);
      setDiscountPct(c.discount_percentage != null ? String(c.discount_percentage) : "");
      setDeliveryDay(c.delivery_day || "thursday");
      setDirty(false);
    } catch {
      toast.error("Fout bij laden klant");
    } finally {
      setLoading(false);
    }
  }, [customerId]);

  useEffect(() => { load(); }, [load]);

  async function save() {
    if (!customer) return;
    setSaving(true);
    try {
      const dp = discountPct.trim() === "" ? null : parseFloat(discountPct);
      if (dp !== null && (isNaN(dp) || dp < 0 || dp > 100)) {
        toast.error("Korting moet tussen 0 en 100 zijn");
        setSaving(false);
        return;
      }
      await api.updateCustomer(customer.id, {
        name: name.trim(),
        show_prices: showPrices,
        discount_percentage: dp,
        delivery_day: deliveryDay,
      });
      toast.success("Klant opgeslagen");
      load();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout bij opslaan");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!customer) return;
    if (!confirm(`Klant '${customer.name}' verwijderen? Dit kan niet ongedaan worden.`)) return;
    try {
      await api.deleteCustomer(customer.id);
      toast.success("Klant verwijderd");
      onBack();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout bij verwijderen");
    }
  }

  async function handleRemoveSKU(skuId: number, skuName: string) {
    if (!confirm(`'${skuName}' verwijderen uit assortiment?`)) return;
    try {
      await api.removeCustomerSKU(customerId, skuId);
      toast.success("Product verwijderd uit assortiment");
      load();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout bij verwijderen");
    }
  }

  function markDirty(setter: (v: string) => void, value: string) {
    setter(value);
    setDirty(true);
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <Card className="p-6 animate-pulse h-40" />
        <Card className="p-6 animate-pulse h-60" />
      </div>
    );
  }

  if (!customer) {
    return <p className="text-center text-muted-foreground py-8">Klant niet gevonden</p>;
  }

  const formatPrice = (p: number | null) =>
    p != null ? `€${p.toFixed(2)}` : "-";

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon" onClick={onBack}>
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <h2 className="text-xl font-bold">{customer.name}</h2>
        </div>
        <div className="flex gap-2">
          <Button
            variant="destructive"
            size="sm"
            onClick={handleDelete}
          >
            Verwijderen
          </Button>
          <Button size="sm" onClick={save} disabled={!dirty || saving}>
            {saving ? "Opslaan..." : "Opslaan"}
          </Button>
        </div>
      </div>

      {/* General settings */}
      <Card className="p-4 space-y-4">
        <h3 className="font-semibold">Algemeen</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="space-y-1">
            <Label>Naam</Label>
            <Input
              value={name}
              onChange={(e) => markDirty(setName, e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label>Standaard korting (%)</Label>
            <Input
              type="number"
              min="0"
              max="100"
              step="0.01"
              placeholder="Geen"
              value={discountPct}
              onChange={(e) => markDirty(setDiscountPct, e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label>Leverdag</Label>
            <Select
              value={deliveryDay}
              onValueChange={(v) => { setDeliveryDay(v); setDirty(true); }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="wednesday">Woensdag</SelectItem>
                <SelectItem value="thursday">Donderdag</SelectItem>
                <SelectItem value="friday">Vrijdag</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-end gap-2 pb-1">
            <Switch
              checked={showPrices}
              onCheckedChange={(v) => { setShowPrices(v); setDirty(true); }}
            />
            <Label>Prijzen tonen</Label>
          </div>
        </div>
      </Card>

      {/* Product assortment */}
      <Card className="p-4 space-y-4">
        <div className="flex justify-between items-center">
          <h3 className="font-semibold">
            Assortiment
            {skus.length > 0 && (
              <Badge variant="secondary" className="ml-2">{skus.length}</Badge>
            )}
          </h3>
          <Button size="sm" variant="outline" onClick={() => setShowAddProduct(true)}>
            <Plus className="h-4 w-4 mr-1" />
            Product toevoegen
          </Button>
        </div>

        {skus.length === 0 ? (
          <p className="text-center text-muted-foreground py-4">
            Geen producten in assortiment
          </p>
        ) : (
          <div className="border rounded-md overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>SKU</TableHead>
                  <TableHead>Naam</TableHead>
                  <TableHead className="text-right">Standaardprijs</TableHead>
                  <TableHead className="text-right">Klantprijs</TableHead>
                  <TableHead className="text-right">Korting</TableHead>
                  <TableHead className="text-right">Effectieve prijs</TableHead>
                  <TableHead className="w-[50px]" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {skus.map((s) => {
                  const hasSpecificDiscount = s.discount_type != null;
                  const usesCustomerDiscount =
                    !hasSpecificDiscount &&
                    s.unit_price == null &&
                    customer.discount_percentage != null;

                  return (
                    <TableRow key={s.sku_id}>
                      <TableCell className="font-mono text-sm">{s.sku_code}</TableCell>
                      <TableCell>{s.sku_name}</TableCell>
                      <TableCell className="text-right">{formatPrice(s.default_price)}</TableCell>
                      <TableCell className="text-right">
                        {s.unit_price != null ? formatPrice(s.unit_price) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        {hasSpecificDiscount ? (
                          <Badge variant="outline">
                            {s.discount_type === "percentage"
                              ? `${s.discount_value}%`
                              : `€${s.discount_value?.toFixed(2)}`}
                          </Badge>
                        ) : usesCustomerDiscount ? (
                          <Badge variant="secondary">
                            {customer.discount_percentage}%
                          </Badge>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right font-medium">
                        {formatPrice(s.effective_price)}
                      </TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleRemoveSKU(s.sku_id, s.sku_name)}
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </Card>

      <AddProductDialog
        open={showAddProduct}
        onClose={() => setShowAddProduct(false)}
        customerId={customerId}
        existingSkuIds={skus.map((s) => s.sku_id)}
        onAdded={() => { setShowAddProduct(false); load(); }}
      />
    </div>
  );
}

// ── New customer dialog ──────────────────────────────────────────────

function NewCustomerDialog({
  open,
  onClose,
  onCreated,
  isAdmin,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
  isAdmin: boolean;
}) {
  const [name, setName] = useState("");
  const [orgId, setOrgId] = useState<number | "">("");
  const [organizations, setOrganizations] = useState<{ id: number; name: string }[]>([]);

  useEffect(() => {
    if (open) {
      setName("");
      setOrgId("");
      if (isAdmin) {
        api.listOrganizations().then(setOrganizations).catch(() => {});
      }
    }
  }, [open, isAdmin]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.createCustomer({
        name,
        organization_id: orgId ? (orgId as number) : undefined,
      });
      toast.success("Klant aangemaakt");
      onCreated();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout bij aanmaken");
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Nieuwe klant</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label>Naam</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          {isAdmin && organizations.length > 0 && (
            <div className="space-y-2">
              <Label>Organisatie</Label>
              <select
                className="w-full border rounded px-3 py-2 text-sm"
                value={orgId}
                onChange={(e) => setOrgId(e.target.value ? Number(e.target.value) : "")}
              >
                <option value="">Selecteer...</option>
                {organizations.map((o) => (
                  <option key={o.id} value={o.id}>{o.name}</option>
                ))}
              </select>
            </div>
          )}
          <Button type="submit" className="w-full">Aanmaken</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ── Add product dialog ───────────────────────────────────────────────

function AddProductDialog({
  open,
  onClose,
  customerId,
  existingSkuIds,
  onAdded,
}: {
  open: boolean;
  onClose: () => void;
  customerId: number;
  existingSkuIds: number[];
  onAdded: () => void;
}) {
  const [allSKUs, setAllSKUs] = useState<SKU[]>([]);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    if (open) {
      setSearch("");
      setSelected(new Set());
      setLoading(true);
      api.listSKUs(true).then((skus: SKU[]) => {
        setAllSKUs(skus);
        setLoading(false);
      }).catch(() => {
        toast.error("Fout bij laden producten");
        setLoading(false);
      });
    }
  }, [open]);

  const existingSet = new Set(existingSkuIds);
  const available = allSKUs.filter((s) => !existingSet.has(s.id));
  const filtered = search.trim()
    ? available.filter(
        (s) =>
          s.name.toLowerCase().includes(search.toLowerCase()) ||
          s.sku_code.toLowerCase().includes(search.toLowerCase())
      )
    : available;

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function submit() {
    if (selected.size === 0) return;
    setAdding(true);
    try {
      await api.addCustomerSKUs(customerId, Array.from(selected));
      toast.success(`${selected.size} product(en) toegevoegd`);
      onAdded();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout bij toevoegen");
    } finally {
      setAdding(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-lg max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>Producten toevoegen</DialogTitle>
        </DialogHeader>

        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Zoek op naam of SKU..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>

        <div className="flex-1 overflow-y-auto border rounded-md min-h-[200px] max-h-[400px]">
          {loading ? (
            <p className="text-center text-muted-foreground py-8">Laden...</p>
          ) : filtered.length === 0 ? (
            <p className="text-center text-muted-foreground py-8">
              {available.length === 0
                ? "Alle producten zijn al toegevoegd"
                : "Geen resultaten"}
            </p>
          ) : (
            <div className="divide-y">
              {filtered.map((s) => (
                <label
                  key={s.id}
                  className="flex items-center gap-3 px-3 py-2 hover:bg-muted/50 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={selected.has(s.id)}
                    onChange={() => toggle(s.id)}
                    className="rounded"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium truncate">{s.name}</div>
                    <div className="text-xs text-muted-foreground font-mono">{s.sku_code}</div>
                  </div>
                </label>
              ))}
            </div>
          )}
        </div>

        <div className="flex justify-between items-center pt-2">
          <span className="text-sm text-muted-foreground">
            {selected.size} geselecteerd
          </span>
          <div className="flex gap-2">
            <Button variant="outline" onClick={onClose}>Annuleren</Button>
            <Button onClick={submit} disabled={selected.size === 0 || adding}>
              {adding ? "Toevoegen..." : "Toevoegen"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
