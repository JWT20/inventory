import { useState, useEffect, useCallback, useRef } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { useAuth, hasModule } from "@/lib/auth";
import {
  LOCATION_LABELS,
  LOCATION_TITLES,
  type InventoryLocation,
} from "@/lib/inventory-locations";
import { Input } from "@/components/ui/input";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogBody,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";

interface CustomerPrice {
  customer_id: number;
  customer_name: string;
  unit_price: number | null;
  discount_type: string | null;
  discount_value: number | null;
  effective_price: number | null;
}

interface InventoryItem {
  sku_id: number;
  sku_code: string;
  sku_name: string;
  active: boolean;
  attributes: Record<string, string>;
  ean: string | null;
  default_price: number | null;
  inventory_location: InventoryLocation;
  quantity_on_hand: number;
  quantity_reserved: number;
  quantity_available: number;
  last_movement_at: string | null;
  image_url: string | null;
  customer_prices: CustomerPrice[];
}

interface Organization {
  id: number;
  name: string;
}

const LOW_STOCK_THRESHOLD = 3;

export function parseTransferQuantity(value: string): number | null {
  const quantity = Number(value);
  return Number.isInteger(quantity) && quantity > 0 ? quantity : null;
}

function thumbnailSrcSet(url: string) {
  const largeUrl = url.replace("/api/thumbnails/112/", "/api/thumbnails/224/");
  return `${url} 1x, ${largeUrl} 2x`;
}

function InventoryCardSkeleton() {
  return (
    <Card className="p-4">
      <div className="flex gap-3">
        <Skeleton className="w-14 h-14 rounded flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="flex justify-between items-start">
            <div className="min-w-0">
              <Skeleton className="h-5 w-40" />
              <Skeleton className="h-4 w-32 mt-1" />
            </div>
            <Skeleton className="h-7 w-10 ml-2" />
          </div>
        </div>
      </div>
    </Card>
  );
}

export function InventoryPage() {
  const { user } = useAuth();
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [selectedOrganizationId, setSelectedOrganizationId] = useState("");
  const [inventoryLocation, setInventoryLocation] = useState<InventoryLocation>("warehouse");
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<InventoryItem | null>(null);
  const needsOrganizationSelection = !!user && (user.is_platform_admin || user.role === "courier");
  const canViewPrices = !!user && user.role !== "courier";
  // The shop shelf is the merchant's own; couriers only ever work the warehouse.
  const canSwitchLocation = !!user && user.role !== "courier";
  // EAN orgs scan a barcode into the search box, so advertise it in the hint.
  const canBarcode = hasModule(user, "barcode_picking");

  // Debounce the search box so a scanner burst (or each keystroke) doesn't hit
  // the server once per character.
  useEffect(() => {
    const t = window.setTimeout(() => setQuery(search.trim()), 300);
    return () => window.clearTimeout(t);
  }, [search]);

  const load = useCallback(async () => {
    if (needsOrganizationSelection && !selectedOrganizationId) {
      setItems([]);
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      const params = new URLSearchParams();
      if (query) params.set("search", query);
      if (selectedOrganizationId) {
        params.set("organization_id", selectedOrganizationId);
      }
      params.set("inventory_location", inventoryLocation);
      const qs = params.toString();
      setItems(await api.listInventoryOverview(qs ? `?${qs}` : ""));
    } catch {
      toast.error("Kan voorraad niet laden");
    } finally {
      setLoading(false);
    }
  }, [inventoryLocation, needsOrganizationSelection, query, selectedOrganizationId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!needsOrganizationSelection) return;
    api.listOrganizations()
      .then((orgs: Organization[]) => setOrganizations(orgs))
      .catch(() => toast.error("Kan handelaren niet laden"));
  }, [needsOrganizationSelection]);

  return (
    <>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Voorraad</h2>
      </div>

      {needsOrganizationSelection && (
        <div className="mb-4">
          <Select value={selectedOrganizationId} onValueChange={setSelectedOrganizationId}>
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
      )}

      {canSwitchLocation && (
        <div className="mb-4">
          <Select
            value={inventoryLocation}
            onValueChange={(value) => {
              setInventoryLocation(value as InventoryLocation);
              setSelected(null);
            }}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="warehouse">Magazijnvoorraad</SelectItem>
              <SelectItem value="webshop">Webshopvoorraad</SelectItem>
              <SelectItem value="store">Winkelvoorraad</SelectItem>
            </SelectContent>
          </Select>
        </div>
      )}

      <Input
        placeholder={
          canBarcode
            ? "Zoek op naam, EAN of leverancier..."
            : "Zoek op naam of leverancier..."
        }
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="mb-4"
        disabled={needsOrganizationSelection && !selectedOrganizationId}
      />

      <div className="space-y-3">
        {needsOrganizationSelection && !selectedOrganizationId ? (
          <p className="text-center text-muted-foreground py-10">
            Kies een handelaar om voorraad te bekijken
          </p>
        ) : loading ? (
          Array.from({ length: 4 }).map((_, i) => <InventoryCardSkeleton key={i} />)
        ) : items.length === 0 ? (
          <p className="text-center text-muted-foreground py-10">
            Geen voorraad gevonden
          </p>
        ) : (
          items.map((item) => (
            <Card
              key={item.sku_id}
              className="p-4 cursor-pointer active:scale-[0.98] transition-transform"
              onClick={() => setSelected(item)}
            >
              <div className="flex gap-3">
                {item.image_url && (
                  <img
                    src={item.image_url}
                    srcSet={thumbnailSrcSet(item.image_url)}
                    sizes="56px"
                    alt={item.sku_name}
                    width={56}
                    height={56}
                    loading="lazy"
                    decoding="async"
                    className="w-14 h-14 object-cover rounded border border-border flex-shrink-0"
                  />
                )}
                <div className="flex-1 min-w-0">
                  <div className="flex justify-between items-start">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="font-semibold truncate">{item.sku_name}</p>
                        {!item.active && (
                          <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                            Inactief
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-muted-foreground">
                        {item.attributes.producent || ""}{" "}
                        {item.attributes.wijntype ? `\u00B7 ${item.attributes.wijntype}` : ""}
                        {item.attributes.volume ? ` \u00B7 ${item.attributes.volume}ml` : ""}
                      </p>
                      {item.ean && (
                        <p className="text-sm text-muted-foreground">EAN {item.ean}</p>
                      )}
                    </div>
                    <div className="text-right flex-shrink-0 ml-2">
                      <p
                        className={`text-lg font-bold ${
                          item.quantity_available < LOW_STOCK_THRESHOLD
                            ? "text-red-600"
                            : ""
                        }`}
                      >
                        {item.quantity_available}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {item.quantity_on_hand} totaal
                      </p>
                      {canViewPrices && item.default_price != null && (
                        <p className="text-sm text-muted-foreground">
                          {"\u20AC"}{item.default_price.toFixed(2)}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </Card>
          ))
        )}
      </div>

      <InventoryDetailDialog
        item={selected}
        canViewPrices={canViewPrices}
        organizationId={selectedOrganizationId ? Number(selectedOrganizationId) : null}
        onClose={() => setSelected(null)}
        onUpdated={(updated) => {
          setItems((prev) =>
            prev.map((i) => (i.sku_id === updated.sku_id ? updated : i))
          );
          setSelected(updated);
        }}
        onRefresh={load}
      />
    </>
  );
}

function InventoryDetailDialog({
  item,
  canViewPrices,
  organizationId,
  onClose,
  onUpdated,
  onRefresh,
}: {
  item: InventoryItem | null;
  canViewPrices: boolean;
  organizationId: number | null;
  onClose: () => void;
  onUpdated: (item: InventoryItem) => void;
  onRefresh: () => void;
}) {
  const { user } = useAuth();
  const needsOrganizationSelection =
    !!user && (user.is_platform_admin || user.role === "courier");
  const canAdjustStock =
    !!user &&
    (user.is_platform_admin ||
      user.role === "owner" ||
      user.role === "member" ||
      user.role === "courier") &&
    (!needsOrganizationSelection || organizationId != null);
  const canManagePrices = canViewPrices && canAdjustStock;
  // Couriers work the warehouse; deciding what goes onto the shop shelf is the
  // merchant's call, so they never get the move action.
  const destination = item?.inventory_location === "store" ? "warehouse" : "store";
  const destinationLabel = LOCATION_LABELS[destination];
  // Couriers work the warehouse; deciding what goes onto the shop shelf is the
  // merchant's call, so they never get the move action. The webshop pool has no
  // move action yet — it gets its own destination picker in the follow-up.
  const canTransferStock =
    canAdjustStock &&
    !!user &&
    user.role !== "courier" &&
    item?.inventory_location !== "webshop";

  const [editingDefaultPrice, setEditingDefaultPrice] = useState(false);
  const [defaultPriceValue, setDefaultPriceValue] = useState("");
  const [editingCustomerPriceId, setEditingCustomerPriceId] = useState<number | null>(null);
  const [customerPriceValue, setCustomerPriceValue] = useState("");
  const [editingDiscountId, setEditingDiscountId] = useState<number | null>(null);
  const [discountType, setDiscountType] = useState<string>("");
  const [discountValue, setDiscountValue] = useState("");
  const [editingStock, setEditingStock] = useState(false);
  const [stockDeltaValue, setStockDeltaValue] = useState("");
  const [stockNoteValue, setStockNoteValue] = useState("");
  const [savingStock, setSavingStock] = useState(false);
  const [movingStock, setMovingStock] = useState(false);
  const [transferValue, setTransferValue] = useState("");
  const [transferNoteValue, setTransferNoteValue] = useState("");
  const [savingTransfer, setSavingTransfer] = useState(false);
  const transferInFlight = useRef(false);

  useEffect(() => {
    if (item) {
      setEditingDefaultPrice(false);
      setEditingCustomerPriceId(null);
      setEditingDiscountId(null);
      setEditingStock(false);
      setStockDeltaValue("");
      setStockNoteValue("");
      setMovingStock(false);
      setTransferValue("");
      setTransferNoteValue("");
    }
  }, [item]);

  if (!item) return null;

  async function saveTransfer() {
    if (!item || transferInFlight.current) return;
    const from = item.inventory_location;
    // The webshop pool has no move action yet; its destination picker lands in
    // the follow-up PR.
    if (from === "webshop") return;
    const quantity = parseTransferQuantity(transferValue);
    if (quantity == null) {
      toast.error("Vul een aantal groter dan 0 in");
      return;
    }
    if (quantity > item.quantity_available) {
      toast.error(
        `Er zijn maar ${item.quantity_available} beschikbaar; de rest is gereserveerd`,
      );
      return;
    }
    transferInFlight.current = true;
    setSavingTransfer(true);
    try {
      await api.transferInventory(
        item.sku_id,
        quantity,
        from,
        destination,
        transferNoteValue.trim() || null,
        organizationId,
      );
      setMovingStock(false);
      setTransferValue("");
      setTransferNoteValue("");
      toast.success(`${quantity} verplaatst naar ${destinationLabel}`);
      // Both pools changed, and the list only shows one of them.
      onRefresh();
      onClose();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Verplaatsen mislukt");
    } finally {
      transferInFlight.current = false;
      setSavingTransfer(false);
    }
  }

  async function saveStockAdjustment() {
    if (!item) return;
    const delta = parseInt(stockDeltaValue, 10);
    if (!Number.isFinite(delta) || delta === 0) {
      toast.error("Vul een aantal in (bijv. -3 of 10)");
      return;
    }
    if (delta < 0) {
      const ok = window.confirm(
        `Voorraad verlagen met ${Math.abs(delta)}? Dit kan niet ongedaan worden gemaakt.`,
      );
      if (!ok) return;
    }
    const note = stockNoteValue.trim() || null;
    setSavingStock(true);
    try {
      await api.adjustInventory(
        item.sku_id,
        delta,
        note,
        organizationId,
        item.inventory_location,
      );
      const quantityOnHand = item.quantity_on_hand + delta;
      onUpdated({
        ...item,
        quantity_on_hand: quantityOnHand,
        quantity_available: Math.max(quantityOnHand - item.quantity_reserved, 0),
      });
      setEditingStock(false);
      setStockDeltaValue("");
      setStockNoteValue("");
      toast.success("Voorraad aangepast");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout bij opslaan");
    } finally {
      setSavingStock(false);
    }
  }

  async function saveDefaultPrice() {
    if (!item) return;
    try {
      const price = defaultPriceValue.trim() === "" ? null : parseFloat(defaultPriceValue);
      const updated = await api.updateDefaultPrice(item.sku_id, price);
      onUpdated({ ...item, default_price: updated.default_price });
      setEditingDefaultPrice(false);
      toast.success("Standaardprijs opgeslagen");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout bij opslaan");
    }
  }

  async function saveCustomerPrice(customerId: number) {
    if (!item) return;
    try {
      const price = customerPriceValue.trim() === "" ? null : parseFloat(customerPriceValue);
      await api.updateCustomerPrice(customerId, item.sku_id, price);
      const updatedPrices = item.customer_prices.map((cp) =>
        cp.customer_id === customerId ? { ...cp, unit_price: price } : cp
      );
      onUpdated({ ...item, customer_prices: updatedPrices });
      setEditingCustomerPriceId(null);
      toast.success("Klantprijs opgeslagen");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout bij opslaan");
    }
  }

  async function saveDiscount(customerId: number) {
    if (!item) return;
    try {
      const dt = discountType || null;
      const dv = discountValue.trim() === "" ? null : parseFloat(discountValue);
      const result = await api.updateCustomerSKUDiscount(customerId, item.sku_id, dt, dv);
      const updatedPrices = item.customer_prices.map((cp) =>
        cp.customer_id === customerId
          ? { ...cp, discount_type: dt, discount_value: dv, effective_price: result.effective_price }
          : cp
      );
      onUpdated({ ...item, customer_prices: updatedPrices });
      setEditingDiscountId(null);
      toast.success("Korting opgeslagen");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout bij opslaan");
    }
  }

  return (
    <Dialog open={!!item} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {item.sku_name}
            <span className="ml-2 text-sm font-normal text-muted-foreground">
              {item.sku_code}
            </span>
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              {LOCATION_TITLES[item.inventory_location]}
            </span>
          </DialogTitle>
        </DialogHeader>

        <DialogBody>
        <div className="space-y-4">
          {/* Stock */}
          <div className="space-y-2">
            <div className="flex justify-between items-start">
              <span className="text-sm text-muted-foreground">Voorraad</span>
              <div className="flex items-center gap-3">
                {canAdjustStock && !editingStock && !movingStock && (
                  <>
                    <button
                      onClick={() => {
                        setStockDeltaValue("");
                        setStockNoteValue("");
                        setEditingStock(true);
                      }}
                      className="text-sm text-primary hover:underline"
                    >
                      Aanpassen
                    </button>
                    {canTransferStock && (
                      <button
                        onClick={() => {
                          setTransferValue("");
                          setTransferNoteValue("");
                          setMovingStock(true);
                        }}
                        className="text-sm text-primary hover:underline"
                      >
                        Verplaatsen
                      </button>
                    )}
                  </>
                )}
              </div>
            </div>
            <div className="grid grid-cols-3 gap-2 text-sm">
              <div className="rounded-md border border-border p-2">
                <p className="text-xs text-muted-foreground">Op voorraad</p>
                <p className="font-semibold">{item.quantity_on_hand}</p>
              </div>
              <div className="rounded-md border border-border p-2">
                <p className="text-xs text-muted-foreground">Gereserveerd</p>
                <p className="font-semibold">{item.quantity_reserved}</p>
              </div>
              <div className="rounded-md border border-border p-2">
                <p className="text-xs text-muted-foreground">Beschikbaar</p>
                <p
                  className={`font-semibold ${
                    item.quantity_available < LOW_STOCK_THRESHOLD ? "text-red-600" : ""
                  }`}
                >
                  {item.quantity_available}
                </p>
              </div>
            </div>
            {canAdjustStock && editingStock && (
              <div className="rounded-md border border-border p-3 space-y-2">
                <label className="text-xs text-muted-foreground block">
                  Aantal (gebruik negatief om te verlagen, bv. -3)
                </label>
                <Input
                  type="number"
                  step="1"
                  value={stockDeltaValue}
                  onChange={(e) => setStockDeltaValue(e.target.value)}
                  className="h-8 text-sm"
                  placeholder="bv. 12 of -3"
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === "Enter") saveStockAdjustment();
                    if (e.key === "Escape") setEditingStock(false);
                  }}
                />
                <Input
                  type="text"
                  value={stockNoteValue}
                  onChange={(e) => setStockNoteValue(e.target.value)}
                  className="h-8 text-sm"
                  placeholder="Reden (optioneel)"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") saveStockAdjustment();
                    if (e.key === "Escape") setEditingStock(false);
                  }}
                />
                <div className="flex items-center gap-3 pt-1">
                  <button
                    onClick={saveStockAdjustment}
                    disabled={savingStock}
                    className="text-sm text-primary hover:underline disabled:opacity-50"
                  >
                    {savingStock ? "Opslaan..." : "Opslaan"}
                  </button>
                  <button
                    onClick={() => setEditingStock(false)}
                    disabled={savingStock}
                    className="text-sm text-muted-foreground hover:underline disabled:opacity-50"
                  >
                    Annuleren
                  </button>
                </div>
              </div>
            )}
            {canTransferStock && movingStock && (
              <div className="rounded-md border border-border p-3 space-y-2">
                <label className="text-xs text-muted-foreground block">
                  Aantal naar {destinationLabel} (max {item.quantity_available}{" "}
                  beschikbaar)
                </label>
                <Input
                  type="number"
                  step="1"
                  min="1"
                  value={transferValue}
                  onChange={(e) => setTransferValue(e.target.value)}
                  disabled={savingTransfer}
                  className="h-8 text-sm"
                  placeholder="bv. 6"
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === "Enter") saveTransfer();
                    if (e.key === "Escape") setMovingStock(false);
                  }}
                />
                <Input
                  type="text"
                  value={transferNoteValue}
                  onChange={(e) => setTransferNoteValue(e.target.value)}
                  disabled={savingTransfer}
                  className="h-8 text-sm"
                  placeholder="Reden (optioneel)"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") saveTransfer();
                    if (e.key === "Escape") setMovingStock(false);
                  }}
                />
                <div className="flex items-center gap-3 pt-1">
                  <button
                    onClick={saveTransfer}
                    disabled={savingTransfer}
                    className="text-sm text-primary hover:underline disabled:opacity-50"
                  >
                    {savingTransfer
                      ? "Verplaatsen..."
                      : `Naar ${destinationLabel}`}
                  </button>
                  <button
                    onClick={() => setMovingStock(false)}
                    disabled={savingTransfer}
                    className="text-sm text-muted-foreground hover:underline disabled:opacity-50"
                  >
                    Annuleren
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Attributes */}
          <div className="text-sm space-y-1">
            {item.attributes.producent && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Producent</span>
                <span>{item.attributes.producent}</span>
              </div>
            )}
            {item.attributes.wijntype && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Type</span>
                <span>{item.attributes.wijntype}</span>
              </div>
            )}
            {item.attributes.volume && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Volume</span>
                <span>{item.attributes.volume}ml</span>
              </div>
            )}
          </div>

          {/* Default price */}
          {canViewPrices && (
          <>
          <Separator />
          <div className="pt-1">
            <div className="flex flex-wrap justify-between items-center gap-2">
              <span className="text-sm font-medium">Standaardprijs</span>
              {editingDefaultPrice ? (
                <div className="flex items-center gap-2 flex-wrap">
                  <div className="flex items-center gap-1">
                    <span className="text-sm">{"\u20AC"}</span>
                    <Input
                      type="number"
                      step="0.01"
                      value={defaultPriceValue}
                      onChange={(e) => setDefaultPriceValue(e.target.value)}
                      className="w-24 h-8 text-sm"
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === "Enter") saveDefaultPrice();
                        if (e.key === "Escape") setEditingDefaultPrice(false);
                      }}
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={saveDefaultPrice}
                      className="text-sm text-primary hover:underline"
                    >
                      Opslaan
                    </button>
                    <button
                      onClick={() => setEditingDefaultPrice(false)}
                      className="text-sm text-muted-foreground hover:underline"
                    >
                      Annuleren
                    </button>
                  </div>
                </div>
              ) : (
                canManagePrices ? (
                  <button
                    onClick={() => {
                      setDefaultPriceValue(
                        item.default_price != null ? item.default_price.toFixed(2) : ""
                      );
                      setEditingDefaultPrice(true);
                    }}
                    className="text-sm hover:underline"
                  >
                    {item.default_price != null
                      ? `\u20AC${item.default_price.toFixed(2)}`
                      : "Niet ingesteld"}
                  </button>
                ) : (
                  <span className="text-sm">
                    {item.default_price != null
                      ? `\u20AC${item.default_price.toFixed(2)}`
                      : "Niet ingesteld"}
                  </span>
                )
              )}
            </div>
          </div>
          </>
          )}

          {/* Customer prices */}
          {canViewPrices && (
          <>
          <Separator />
          <div className="pt-1">
            <p className="text-sm font-medium mb-2">Klantprijzen</p>
            {item.customer_prices.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Geen klanten gekoppeld aan dit product
              </p>
            ) : (
              <div className="space-y-3">
                {item.customer_prices.map((cp) => (
                  <div key={cp.customer_id} className="border border-border rounded-md p-2 space-y-1">
                    <div className="flex justify-between items-center text-sm">
                      <span className="font-medium">{cp.customer_name}</span>
                      {cp.effective_price != null && (
                        <span className="text-sm font-semibold">
                          {"\u20AC"}{cp.effective_price.toFixed(2)}
                        </span>
                      )}
                    </div>

                    {/* Vaste prijs */}
                    <div className="flex flex-wrap justify-between items-center gap-2 text-sm">
                      <span className="text-muted-foreground">Vaste prijs</span>
                      {editingCustomerPriceId === cp.customer_id ? (
                        <div className="flex items-center gap-2 flex-wrap">
                          <div className="flex items-center gap-1">
                            <span>{"\u20AC"}</span>
                            <Input
                              type="number"
                              step="0.01"
                              value={customerPriceValue}
                              onChange={(e) => setCustomerPriceValue(e.target.value)}
                              className="w-24 h-8 text-sm"
                              autoFocus
                              onKeyDown={(e) => {
                                if (e.key === "Enter") saveCustomerPrice(cp.customer_id);
                                if (e.key === "Escape") setEditingCustomerPriceId(null);
                              }}
                            />
                          </div>
                          <div className="flex items-center gap-2">
                            <button
                              onClick={() => saveCustomerPrice(cp.customer_id)}
                              className="text-primary hover:underline"
                            >
                              Opslaan
                            </button>
                            <button
                              onClick={() => setEditingCustomerPriceId(null)}
                              className="text-muted-foreground hover:underline"
                            >
                              Annuleren
                            </button>
                          </div>
                        </div>
                      ) : (
                        <button
                          onClick={() => {
                            setCustomerPriceValue(
                              cp.unit_price != null ? cp.unit_price.toFixed(2) : ""
                            );
                            setEditingCustomerPriceId(cp.customer_id);
                          }}
                          className="hover:underline"
                        >
                          {cp.unit_price != null
                            ? `\u20AC${cp.unit_price.toFixed(2)}`
                            : "Niet ingesteld"}
                        </button>
                      )}
                    </div>

                    {/* Korting */}
                    <div className="flex flex-wrap justify-between items-center gap-2 text-sm">
                      <span className="text-muted-foreground">Korting</span>
                      {editingDiscountId === cp.customer_id ? (
                        <div className="flex items-center gap-2 flex-wrap">
                          <div className="flex items-center gap-2">
                            <Select
                              value={discountType || "none"}
                              onValueChange={(v) => {
                                const val = v === "none" ? "" : v;
                                setDiscountType(val);
                                if (!val) setDiscountValue("");
                              }}
                            >
                              <SelectTrigger className="h-8 w-24 text-sm">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="none">Geen</SelectItem>
                                <SelectItem value="percentage">%</SelectItem>
                                <SelectItem value="fixed">{"\u20AC"}</SelectItem>
                              </SelectContent>
                            </Select>
                            {discountType && (
                              <Input
                                type="number"
                                step="0.01"
                                value={discountValue}
                                onChange={(e) => setDiscountValue(e.target.value)}
                                className="w-20 h-8 text-sm"
                                placeholder={discountType === "percentage" ? "0-100" : "0.00"}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter") saveDiscount(cp.customer_id);
                                  if (e.key === "Escape") setEditingDiscountId(null);
                                }}
                              />
                            )}
                          </div>
                          <div className="flex items-center gap-2">
                            <button
                              onClick={() => saveDiscount(cp.customer_id)}
                              className="text-primary hover:underline"
                            >
                              Opslaan
                            </button>
                            <button
                              onClick={() => setEditingDiscountId(null)}
                              className="text-muted-foreground hover:underline"
                            >
                              Annuleren
                            </button>
                          </div>
                        </div>
                      ) : (
                        <button
                          onClick={() => {
                            setDiscountType(cp.discount_type || "");
                            setDiscountValue(
                              cp.discount_value != null ? cp.discount_value.toString() : ""
                            );
                            setEditingDiscountId(cp.customer_id);
                          }}
                          className="hover:underline"
                        >
                          {cp.discount_type === "percentage" && cp.discount_value != null
                            ? `-${cp.discount_value}%`
                            : cp.discount_type === "fixed" && cp.discount_value != null
                              ? `-\u20AC${cp.discount_value.toFixed(2)}`
                              : "Geen"}
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
          </>
          )}
        </div>
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
