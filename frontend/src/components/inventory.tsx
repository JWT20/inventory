import { useState, useEffect, useCallback } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

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
  attributes: Record<string, string>;
  default_price: number | null;
  quantity_on_hand: number;
  last_movement_at: string | null;
  image_url: string | null;
  customer_prices: CustomerPrice[];
}

const LOW_STOCK_THRESHOLD = 3;

export function InventoryPage() {
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<InventoryItem | null>(null);

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (search) params.set("search", search);
      const qs = params.toString();
      setItems(await api.listInventoryOverview(qs ? `?${qs}` : ""));
    } catch {
      toast.error("Kan voorraad niet laden");
    }
  }, [search]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Voorraad</h2>
      </div>

      <Input
        placeholder="Zoek op naam, producent..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="mb-4"
      />

      <div className="space-y-3">
        {items.length === 0 ? (
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
                    alt={item.sku_name}
                    className="w-14 h-14 object-cover rounded border border-border flex-shrink-0"
                  />
                )}
                <div className="flex-1 min-w-0">
                  <div className="flex justify-between items-start">
                    <div className="min-w-0">
                      <p className="font-semibold truncate">{item.sku_name}</p>
                      <p className="text-sm text-muted-foreground">
                        {item.attributes.producent || ""}{" "}
                        {item.attributes.wijntype ? `\u00B7 ${item.attributes.wijntype}` : ""}
                        {item.attributes.volume ? ` \u00B7 ${item.attributes.volume}ml` : ""}
                      </p>
                    </div>
                    <div className="text-right flex-shrink-0 ml-2">
                      <p
                        className={`text-lg font-bold ${
                          item.quantity_on_hand < LOW_STOCK_THRESHOLD
                            ? "text-red-600"
                            : ""
                        }`}
                      >
                        {item.quantity_on_hand}
                      </p>
                      {item.default_price != null && (
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
  onClose,
  onUpdated,
  onRefresh,
}: {
  item: InventoryItem | null;
  onClose: () => void;
  onUpdated: (item: InventoryItem) => void;
  onRefresh: () => void;
}) {
  const [editingDefaultPrice, setEditingDefaultPrice] = useState(false);
  const [defaultPriceValue, setDefaultPriceValue] = useState("");
  const [editingCustomerPriceId, setEditingCustomerPriceId] = useState<number | null>(null);
  const [customerPriceValue, setCustomerPriceValue] = useState("");
  const [editingDiscountId, setEditingDiscountId] = useState<number | null>(null);
  const [discountType, setDiscountType] = useState<string>("");
  const [discountValue, setDiscountValue] = useState("");

  useEffect(() => {
    if (item) {
      setEditingDefaultPrice(false);
      setEditingCustomerPriceId(null);
      setEditingDiscountId(null);
    }
  }, [item]);

  if (!item) return null;

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
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          {/* Stock */}
          <div className="flex justify-between items-center">
            <span className="text-sm text-muted-foreground">Voorraad</span>
            <span
              className={`text-lg font-bold ${
                item.quantity_on_hand < LOW_STOCK_THRESHOLD ? "text-red-600" : ""
              }`}
            >
              {item.quantity_on_hand}
            </span>
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
          <div className="border-t border-border pt-3">
            <div className="flex justify-between items-center">
              <span className="text-sm font-medium">Standaardprijs</span>
              {editingDefaultPrice ? (
                <div className="flex items-center gap-2">
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
              ) : (
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
              )}
            </div>
          </div>

          {/* Customer prices */}
          <div className="border-t border-border pt-3">
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
                    <div className="flex justify-between items-center text-sm">
                      <span className="text-muted-foreground">Vaste prijs</span>
                      {editingCustomerPriceId === cp.customer_id ? (
                        <div className="flex items-center gap-2">
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
                    <div className="flex justify-between items-center text-sm">
                      <span className="text-muted-foreground">Korting</span>
                      {editingDiscountId === cp.customer_id ? (
                        <div className="flex items-center gap-2">
                          <select
                            value={discountType}
                            onChange={(e) => {
                              setDiscountType(e.target.value);
                              if (!e.target.value) setDiscountValue("");
                            }}
                            className="h-8 text-sm rounded-md border border-input bg-background px-2"
                          >
                            <option value="">Geen</option>
                            <option value="percentage">%</option>
                            <option value="fixed">{"\u20AC"}</option>
                          </select>
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
        </div>
      </DialogContent>
    </Dialog>
  );
}
