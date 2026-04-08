import { useState, useEffect, useCallback, useRef } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

interface SKUOption {
  id: number;
  sku_code: string;
  name: string;
  category?: string;
  attributes?: Record<string, string>;
}

interface CustomerOption {
  id: number;
  name: string;
  sku_ids: number[];
}

interface OrderLine {
  id: number;
  sku_id: number;
  sku_code: string;
  sku_name: string;
  klant: string;
  customer_id: number | null;
  customer_name: string;
  quantity: number;
  booked_count: number;
  has_image: boolean;
  show_prices: boolean;
  unit_price: number | null;
  discount_type: string | null;
  discount_value: number | null;
  effective_price: number | null;
  line_total: number | null;
}

interface Order {
  id: number;
  reference: string;
  status: string;
  remarks: string;
  organization_name: string;
  created_by_name: string;
  created_at: string;
  lines: OrderLine[];
  total_boxes: number;
  booked_boxes: number;
  visible_total: number | null;
  hidden_lines_count: number;
}

interface CustomerSkuLine {
  sku_id: number;
  checked: boolean;
  quantity: number;
}

const STATUS_LABELS: Record<string, string> = {
  draft: "Concept",
  pending_images: "Wacht op beelden",
  active: "Actief",
  completed: "Voltooid",
  cancelled: "Geannuleerd",
};

const STATUS_VARIANT: Record<string, "active" | "inactive"> = {
  draft: "inactive",
  pending_images: "inactive",
  active: "active",
  completed: "active",
  cancelled: "inactive",
};

const money = new Intl.NumberFormat("nl-NL", {
  style: "currency",
  currency: "EUR",
});

function OrderCardSkeleton() {
  return (
    <Card className="p-4">
      <div className="flex justify-between items-center mb-1">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-5 w-16 rounded-full" />
      </div>
      <Skeleton className="h-4 w-48 mt-2" />
      <Skeleton className="h-4 w-36 mt-1" />
    </Card>
  );
}

export function OrdersPage() {
  const { user } = useAuth();
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);
  const [showManual, setShowManual] = useState(false);

  const load = useCallback(async () => {
    try {
      setOrders(await api.listOrders());
    } catch {
      toast.error("Kan orders niet laden");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Who can create orders?
  const canCreate =
    user &&
    (user.is_platform_admin ||
      user.role === "owner" ||
      user.role === "member" ||
      user.role === "customer");

  return (
    <>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Orders</h2>
        {canCreate && (
          <Button size="sm" onClick={() => setShowManual(true)}>
            + Order
          </Button>
        )}
      </div>

      <div className="space-y-3">
        {loading ? (
          Array.from({ length: 3 }).map((_, i) => <OrderCardSkeleton key={i} />)
        ) : orders.length === 0 ? (
          <p className="text-center text-muted-foreground py-10">
            Geen orders gevonden
          </p>
        ) : (
          orders.map((o) => (
            <Card
              key={o.id}
              className="p-4 cursor-pointer active:scale-[0.98] transition-transform"
              onClick={() => setSelectedOrder(o)}
            >
              <div className="flex justify-between items-center mb-1">
                <span className="font-semibold">{o.reference}</span>
                <Badge variant={STATUS_VARIANT[o.status] ?? "inactive"}>
                  {STATUS_LABELS[o.status] ?? o.status}
                </Badge>
              </div>
              <p className="text-sm text-muted-foreground">
                {o.organization_name || o.created_by_name} &middot;{" "}
                {o.lines.length} product
                {o.lines.length !== 1 ? "en" : ""}
              </p>
              <p className="text-sm text-muted-foreground">
                {o.booked_boxes}/{o.total_boxes} dozen geboekt
              </p>
            </Card>
          ))
        )}
      </div>

      <ManualOrderDialog
        open={showManual}
        onClose={() => setShowManual(false)}
        onCreated={load}
      />

      <OrderDetailDialog
        open={!!selectedOrder}
        order={selectedOrder}
        onClose={() => setSelectedOrder(null)}
        onUpdated={load}
      />
    </>
  );
}

function ManualOrderDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { user } = useAuth();
  const [allSkus, setAllSkus] = useState<SKUOption[]>([]);
  const [allCustomers, setAllCustomers] = useState<CustomerOption[]>([]);
  const [selectedCustomerIds, setSelectedCustomerIds] = useState<number[]>([]);
  const [customerLines, setCustomerLines] = useState<
    Record<number, CustomerSkuLine[]>
  >({});
  const [submitting, setSubmitting] = useState(false);
  const [customerSearch, setCustomerSearch] = useState("");
  const [newCustomerName, setNewCustomerName] = useState("");
  const [creatingCustomer, setCreatingCustomer] = useState(false);
  const [remarks, setRemarks] = useState("");

  const isLinkedCustomer = user?.role === "customer" && !!user?.customer_id;

  useEffect(() => {
    if (!open) return;
    api.listSKUs().then((s: SKUOption[]) => setAllSkus(s));
    api.listCustomers().then((c: CustomerOption[]) => {
      setAllCustomers(c);
      // Auto-select linked customer for customer-role users
      if (isLinkedCustomer && user.customer_id) {
        const linked = c.find((cust) => cust.id === user.customer_id);
        if (linked) {
          setSelectedCustomerIds([linked.id]);
          const lines: CustomerSkuLine[] = (linked.sku_ids || []).map(
            (skuId) => ({ sku_id: skuId, checked: false, quantity: 1 }),
          );
          setCustomerLines({ [linked.id]: lines });
        }
      }
    });
  }, [open]);

  function toggleCustomer(customerId: number) {
    if (selectedCustomerIds.includes(customerId)) {
      setSelectedCustomerIds(
        selectedCustomerIds.filter((id) => id !== customerId),
      );
      return;
    }
    setSelectedCustomerIds([...selectedCustomerIds, customerId]);
    if (!customerLines[customerId]) {
      const customer = allCustomers.find((c) => c.id === customerId);
      const lines: CustomerSkuLine[] = (customer?.sku_ids || []).map(
        (skuId) => ({
          sku_id: skuId,
          checked: false,
          quantity: 1,
        }),
      );
      setCustomerLines((prev) => ({ ...prev, [customerId]: lines }));
    }
  }

  function toggleSkuLine(customerId: number, skuId: number) {
    setCustomerLines((prev) => {
      const lines = [...(prev[customerId] || [])];
      const idx = lines.findIndex((l) => l.sku_id === skuId);
      if (idx >= 0) {
        lines[idx] = { ...lines[idx], checked: !lines[idx].checked };
      }
      return { ...prev, [customerId]: lines };
    });
  }

  function updateSkuQuantity(
    customerId: number,
    skuId: number,
    qty: number,
  ) {
    setCustomerLines((prev) => {
      const lines = [...(prev[customerId] || [])];
      const idx = lines.findIndex((l) => l.sku_id === skuId);
      if (idx >= 0) {
        lines[idx] = { ...lines[idx], quantity: qty, checked: true };
      }
      return { ...prev, [customerId]: lines };
    });
  }

  function addExtraSku(customerId: number, skuId: number) {
    setCustomerLines((prev) => {
      const lines = [...(prev[customerId] || [])];
      if (lines.some((l) => l.sku_id === skuId)) return prev;
      lines.push({ sku_id: skuId, checked: true, quantity: 1 });
      return { ...prev, [customerId]: lines };
    });
  }

  async function handleCreateCustomer() {
    if (!newCustomerName.trim()) return;
    setCreatingCustomer(true);
    try {
      const created = await api.createCustomer({ name: newCustomerName.trim(), organization_id: user?.organization_id });
      setAllCustomers((prev) => [...prev, created]);
      setNewCustomerName("");
      toggleCustomer(created.id);
    } catch (err: unknown) {
      toast.error(
        err instanceof Error ? err.message : "Klant aanmaken mislukt",
      );
    } finally {
      setCreatingCustomer(false);
    }
  }

  async function submit() {
    const orderLines: {
      customer_id: number;
      sku_id: number;
      quantity: number;
    }[] = [];
    for (const customerId of selectedCustomerIds) {
      const lines = customerLines[customerId] || [];
      for (const line of lines) {
        if (line.checked && line.quantity > 0) {
          orderLines.push({
            customer_id: customerId,
            sku_id: line.sku_id,
            quantity: line.quantity,
          });
        }
      }
    }
    if (orderLines.length === 0) {
      toast.error("Selecteer minimaal één product met aantal");
      return;
    }
    setSubmitting(true);
    try {
      await api.createOrder({
        organization_id: user?.organization_id,
        remarks: remarks.trim(),
        lines: orderLines,
      });
      toast.success("Order aangemaakt");
      onCreated();
      onClose();
      setSelectedCustomerIds([]);
      setCustomerLines({});
      setCustomerSearch("");
      setRemarks("");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Aanmaken mislukt");
    } finally {
      setSubmitting(false);
    }
  }

  const filteredCustomers = allCustomers.filter((c) =>
    c.name.toLowerCase().includes(customerSearch.toLowerCase()),
  );

  return (
    <Sheet open={open} onOpenChange={(v) => !v && onClose()}>
      <SheetContent side="bottom" className="max-h-[90vh]">
        <SheetHeader>
          <SheetTitle>Order aanmaken</SheetTitle>
        </SheetHeader>

        <div className="space-y-4">
          {/* Customer selection */}
          {isLinkedCustomer ? (
            <div>
              <Label className="mb-1 block text-sm">Bestellen als</Label>
              <p className="text-sm font-medium px-2 py-1.5 border border-border rounded-md bg-muted">
                {allCustomers.find((c) => c.id === user.customer_id)?.name ?? "..."}
              </p>
            </div>
          ) : (
          <div>
            <Label className="mb-1 block text-sm">Klanten</Label>
            <div className="flex gap-2 mb-2">
              <Input
                placeholder="Zoek klant..."
                value={customerSearch}
                onChange={(e) => setCustomerSearch(e.target.value)}
              />
            </div>
            <div className="max-h-32 overflow-y-auto border border-border rounded-md">
              {filteredCustomers.length === 0 ? (
                <p className="text-xs text-muted-foreground p-2">
                  Geen klanten gevonden
                </p>
              ) : (
                filteredCustomers.map((c) => (
                  <label
                    key={c.id}
                    className="flex items-center gap-2 px-2 py-1.5 hover:bg-muted cursor-pointer text-sm"
                  >
                    <Checkbox
                      checked={selectedCustomerIds.includes(c.id)}
                      onCheckedChange={() => toggleCustomer(c.id)}
                    />
                    <span>{c.name}</span>
                    <span className="text-xs text-muted-foreground ml-auto">
                      {c.sku_ids.length} product
                      {c.sku_ids.length !== 1 ? "en" : ""}
                    </span>
                  </label>
                ))
              )}
            </div>
            {/* Add new customer inline */}
            <div className="flex gap-2 mt-2">
              <Input
                placeholder="Nieuwe klant..."
                value={newCustomerName}
                onChange={(e) => setNewCustomerName(e.target.value)}
                onKeyDown={(e) =>
                  e.key === "Enter" && handleCreateCustomer()
                }
              />
              <Button
                variant="secondary"
                size="sm"
                onClick={handleCreateCustomer}
                disabled={creatingCustomer || !newCustomerName.trim()}
              >
                +
              </Button>
            </div>
          </div>
          )}

          {/* Per-customer SKU selection */}
          {selectedCustomerIds.map((customerId) => {
            const customer = allCustomers.find((c) => c.id === customerId);
            if (!customer) return null;
            const lines = customerLines[customerId] || [];

            return (
              <div key={customerId} className="border border-border rounded-lg">
                <div className="px-3 py-2 bg-muted rounded-t-lg">
                  <span className="font-medium text-sm">{customer.name}</span>
                </div>
                <div className="p-3 space-y-1">
                  {lines.length === 0 ? (
                    <p className="text-xs text-muted-foreground">
                      Geen bekende producten — voeg hieronder toe
                    </p>
                  ) : (
                    lines.map((line) => {
                      const sku = allSkus.find((s) => s.id === line.sku_id);
                      if (!sku) return null;
                      return (
                        <div
                          key={line.sku_id}
                          className="flex items-center gap-2"
                        >
                          <Checkbox
                            checked={line.checked}
                            onCheckedChange={() =>
                              toggleSkuLine(customerId, line.sku_id)
                            }
                          />
                          <span
                            className="text-sm flex-1 truncate"
                            title={`${sku.name} (${sku.sku_code})`}
                          >
                            {sku.name}
                          </span>
                          <Input
                            type="number"
                            min={1}
                            className="w-20 h-7 text-sm"
                            placeholder="Dozen"
                            value={line.checked ? line.quantity : ""}
                            disabled={!line.checked}
                            onChange={(e) =>
                              updateSkuQuantity(
                                customerId,
                                line.sku_id,
                                Number(e.target.value) || 1,
                              )
                            }
                          />
                        </div>
                      );
                    })
                  )}
                  {/* Add other SKU */}
                  <div className="pt-2">
                    <Select
                      value=""
                      onValueChange={(v) => {
                        if (v) addExtraSku(customerId, Number(v));
                      }}
                    >
                      <SelectTrigger className="h-9 text-sm">
                        <SelectValue placeholder="+ Ander product toevoegen..." />
                      </SelectTrigger>
                      <SelectContent>
                        {allSkus
                          .filter(
                            (s) => !lines.some((l) => l.sku_id === s.id),
                          )
                          .map((s) => (
                            <SelectItem key={s.id} value={String(s.id)}>
                              {s.name} ({s.sku_code})
                            </SelectItem>
                          ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </div>
            );
          })}

          <div>
            <Label className="mb-1 block text-sm">Opmerking</Label>
            <Textarea
              placeholder="Optionele opmerking bij dit order..."
              value={remarks}
              onChange={(e) => setRemarks(e.target.value)}
              rows={2}
              className="text-sm"
            />
          </div>

          <Button
            className="w-full"
            onClick={submit}
            disabled={submitting || selectedCustomerIds.length === 0}
          >
            {submitting ? "Aanmaken..." : "Order aanmaken"}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function OrderDetailDialog({
  open,
  order,
  onClose,
  onUpdated,
}: {
  open: boolean;
  order: Order | null;
  onClose: () => void;
  onUpdated: () => void;
}) {
  const { user } = useAuth();
  const [activating, setActivating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [uploadingSkuId, setUploadingSkuId] = useState<number | null>(null);
  const fileRefs = useRef<Record<number, HTMLInputElement | null>>({});
  const [editingRemarks, setEditingRemarks] = useState(false);
  const [remarksValue, setRemarksValue] = useState("");
  const [savingRemarks, setSavingRemarks] = useState(false);

  if (!order) return null;

  const isAdmin = user?.is_platform_admin;
  const isOwner = user?.role === "owner";
  const canManage = isAdmin || isOwner;

  const skusWithoutImages = order.lines.filter((l) => !l.has_image);
  const canActivate =
    (order.status === "draft" || order.status === "pending_images") &&
    skusWithoutImages.length === 0 &&
    canManage;

  async function activate() {
    if (!order) return;
    setActivating(true);
    try {
      await api.activateOrder(order.id);
      toast.success("Order geactiveerd");
      onUpdated();
      onClose();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Activatie mislukt");
    } finally {
      setActivating(false);
    }
  }

  async function handleDelete() {
    if (!order) return;
    if (
      !confirm(
        `Order ${order.reference} verwijderen? Dit kan niet ongedaan worden.`,
      )
    )
      return;
    setDeleting(true);
    try {
      await api.deleteOrder(order.id);
      toast.success("Order verwijderd");
      onUpdated();
      onClose();
    } catch (err: unknown) {
      toast.error(
        err instanceof Error ? err.message : "Verwijderen mislukt",
      );
    } finally {
      setDeleting(false);
    }
  }

  async function saveRemarks() {
    if (!order) return;
    setSavingRemarks(true);
    try {
      await api.updateOrder(order.id, { remarks: remarksValue.trim() });
      toast.success("Opmerking opgeslagen");
      setEditingRemarks(false);
      onUpdated();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Opslaan mislukt");
    } finally {
      setSavingRemarks(false);
    }
  }

  async function handleImageUpload(
    skuId: number,
    e: React.ChangeEvent<HTMLInputElement>,
  ) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadingSkuId(skuId);
    try {
      await api.uploadImage(skuId, file);
      toast.success("Referentiebeeld geüpload");
      onUpdated();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Upload mislukt");
    } finally {
      setUploadingSkuId(null);
      e.target.value = "";
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            Order {order.reference}
            <Badge
              variant={STATUS_VARIANT[order.status] ?? "inactive"}
              className="ml-2"
            >
              {STATUS_LABELS[order.status] ?? order.status}
            </Badge>
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            {order.organization_name}
            {order.created_by_name && ` — ${order.created_by_name}`}
          </p>
          <p className="text-sm text-muted-foreground">
            Voortgang: {order.booked_boxes}/{order.total_boxes} dozen geboekt
          </p>

          <div>
            <Label className="mb-1 block text-sm">Opmerking</Label>
            {editingRemarks ? (
              <div className="space-y-2">
                <Textarea
                  value={remarksValue}
                  onChange={(e) => setRemarksValue(e.target.value)}
                  rows={2}
                  className="text-sm"
                />
                <div className="flex gap-2">
                  <Button size="sm" onClick={saveRemarks} disabled={savingRemarks}>
                    {savingRemarks ? "Opslaan..." : "Opslaan"}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditingRemarks(false)}>
                    Annuleren
                  </Button>
                </div>
              </div>
            ) : (
              <p
                className="text-sm p-2 rounded border border-border bg-card min-h-[2rem] cursor-pointer hover:bg-muted/50"
                onClick={() => {
                  setRemarksValue(order.remarks || "");
                  setEditingRemarks(true);
                }}
              >
                {order.remarks || <span className="text-muted-foreground">Klik om opmerking toe te voegen...</span>}
              </p>
            )}
          </div>

          <div>
            <Label className="mb-2 block">Orderregels</Label>
            <div className="space-y-2">
              {order.lines.map((line) => (
                <div
                  key={line.id}
                  className="flex items-center justify-between text-sm p-2 rounded bg-card border border-border"
                >
                  <div>
                    <p className="font-medium">{line.sku_name}</p>
                    <p className="text-xs text-muted-foreground">
                      {line.sku_code} &middot; Klant:{" "}
                      {line.customer_name || line.klant}
                    </p>
                  </div>
                  <div className="text-right flex items-center gap-2">
                    <div>
                      <p>
                        {line.booked_count}/{line.quantity} dozen
                      </p>
                      {line.show_prices && line.effective_price != null && (
                        <p className="text-xs text-muted-foreground">
                          {money.format(line.effective_price)} p/st ·{" "}
                          {line.line_total != null
                            ? money.format(line.line_total)
                            : "—"}
                        </p>
                      )}
                      {!line.show_prices && (
                        <p className="text-xs text-muted-foreground">
                          Prijs verborgen
                        </p>
                      )}
                    </div>
                    {!line.has_image && canManage && (
                      <>
                        <Button
                          variant="secondary"
                          size="sm"
                          className="text-xs h-7 px-2"
                          disabled={uploadingSkuId === line.sku_id}
                          onClick={() =>
                            fileRefs.current[line.sku_id]?.click()
                          }
                        >
                          {uploadingSkuId === line.sku_id
                            ? "Uploaden..."
                            : "Foto"}
                        </Button>
                        <input
                          ref={(el) => {
                            fileRefs.current[line.sku_id] = el;
                          }}
                          type="file"
                          accept="image/*"
                          capture="environment"
                          className="hidden"
                          onChange={(e) =>
                            handleImageUpload(line.sku_id, e)
                          }
                        />
                      </>
                    )}
                    {!line.has_image && !canManage && (
                      <p className="text-xs text-amber-400">Geen beeld</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {skusWithoutImages.length > 0 && (
            <div className="p-3 bg-amber-600/20 border border-amber-600 rounded-lg">
              <p className="text-sm text-amber-400">
                {skusWithoutImages.length} SKU('s) zonder referentiebeeld.
                Upload een foto per SKU hierboven.
              </p>
            </div>
          )}

          {order.visible_total != null && (
            <div className="p-3 rounded border border-border bg-card flex items-center justify-between text-sm">
              <span className="text-muted-foreground">Totaal zichtbaar</span>
              <span className="font-semibold">{money.format(order.visible_total)}</span>
            </div>
          )}
          {order.hidden_lines_count > 0 && (
            <p className="text-xs text-muted-foreground">
              {order.hidden_lines_count} orderregel(s) hebben verborgen prijzen.
            </p>
          )}

          {canActivate && (
            <Button
              className="w-full"
              onClick={activate}
              disabled={activating}
            >
              {activating ? "Activeren..." : "Order activeren"}
            </Button>
          )}

          {isAdmin && (
            <Button
              variant="destructive"
              className="w-full"
              onClick={handleDelete}
              disabled={deleting}
            >
              {deleting ? "Verwijderen..." : "Order verwijderen"}
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
