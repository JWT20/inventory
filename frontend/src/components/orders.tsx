import { useState, useEffect, useCallback } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface OrderLine {
  id: number;
  sku_id: number;
  sku_code: string;
  sku_name: string;
  quantity: number;
  picked_quantity: number;
  status: string;
}

interface Order {
  id: number;
  order_number: string;
  customer_name: string;
  status: string;
  lines: OrderLine[];
}

interface SKU {
  id: number;
  sku_code: string;
  name: string;
}

const statusLabel: Record<string, string> = {
  pending: "Open",
  picking: "Bezig",
  completed: "Klaar",
};

export function OrdersPage() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [filter, setFilter] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [detail, setDetail] = useState<Order | null>(null);

  const load = useCallback(async () => {
    try {
      setOrders(await api.listOrders(filter));
    } catch {
      toast.error("Kan orders niet laden");
    }
  }, [filter]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Orders</h2>
        <Button size="sm" onClick={() => setShowNew(true)}>
          + Nieuw
        </Button>
      </div>

      <div className="flex gap-2 mb-4 overflow-x-auto">
        {[
          { value: "", label: "Alle" },
          { value: "pending", label: "Open" },
          { value: "picking", label: "Bezig" },
          { value: "completed", label: "Klaar" },
        ].map((f) => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
            className={`px-4 py-1.5 rounded-full text-sm font-medium border transition-colors whitespace-nowrap ${
              filter === f.value
                ? "bg-primary text-primary-foreground border-primary"
                : "border-border text-muted-foreground hover:text-foreground"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="space-y-3">
        {orders.length === 0 ? (
          <p className="text-center text-muted-foreground py-10">
            Geen orders gevonden
          </p>
        ) : (
          orders.map((o) => {
            const total = o.lines.reduce((s, l) => s + l.quantity, 0);
            const picked = o.lines.reduce((s, l) => s + l.picked_quantity, 0);
            return (
              <Card
                key={o.id}
                className="p-4 cursor-pointer active:scale-[0.98] transition-transform"
                onClick={() => setDetail(o)}
              >
                <div className="flex justify-between items-center mb-1">
                  <span className="font-semibold">{o.order_number}</span>
                  <Badge
                    variant={
                      o.status as "pending" | "picking" | "completed"
                    }
                  >
                    {statusLabel[o.status] || o.status}
                  </Badge>
                </div>
                <p className="text-sm text-muted-foreground">
                  {o.customer_name}
                </p>
                <p className="text-sm text-muted-foreground mt-1">
                  {picked}/{total} gepickt &bull; {o.lines.length} regels
                </p>
              </Card>
            );
          })
        )}
      </div>

      <NewOrderDialog open={showNew} onClose={() => setShowNew(false)} onCreated={load} />
      <OrderDetailDialog order={detail} onClose={() => setDetail(null)} onChanged={load} />
    </>
  );
}

function NewOrderDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [orderNumber, setOrderNumber] = useState("");
  const [customerName, setCustomerName] = useState("");
  const [lines, setLines] = useState<{ sku_code: string; quantity: number }[]>(
    []
  );
  const [skus, setSkus] = useState<SKU[]>([]);

  useEffect(() => {
    if (open) {
      api.listSKUs(true).then(setSkus).catch(() => {});
      setOrderNumber("");
      setCustomerName("");
      setLines([]);
    }
  }, [open]);

  function addLine() {
    if (skus.length === 0) return;
    setLines([...lines, { sku_code: skus[0].sku_code, quantity: 1 }]);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (lines.length === 0) {
      toast.error("Voeg minstens 1 regel toe");
      return;
    }
    try {
      await api.createOrder({
        order_number: orderNumber,
        customer_name: customerName,
        lines,
      });
      toast.success("Order aangemaakt");
      onClose();
      onCreated();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout");
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Nieuwe Order</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label>Ordernummer</Label>
            <Input
              value={orderNumber}
              onChange={(e) => setOrderNumber(e.target.value)}
              required
            />
          </div>
          <div className="space-y-2">
            <Label>Klant</Label>
            <Input
              value={customerName}
              onChange={(e) => setCustomerName(e.target.value)}
              required
            />
          </div>
          <div>
            <Label>Orderregels</Label>
            <div className="space-y-2 mt-2">
              {lines.map((line, i) => (
                <div key={i} className="flex gap-2 items-center">
                  <select
                    className="flex-[2] h-10 rounded-md border border-border bg-input px-2 text-sm text-foreground"
                    value={line.sku_code}
                    onChange={(e) => {
                      const next = [...lines];
                      next[i].sku_code = e.target.value;
                      setLines(next);
                    }}
                  >
                    {skus.map((s) => (
                      <option key={s.sku_code} value={s.sku_code}>
                        {s.name} ({s.sku_code})
                      </option>
                    ))}
                  </select>
                  <Input
                    type="number"
                    min={1}
                    className="flex-1"
                    value={line.quantity}
                    onChange={(e) => {
                      const next = [...lines];
                      next[i].quantity = parseInt(e.target.value) || 1;
                      setLines(next);
                    }}
                  />
                  <Button
                    type="button"
                    variant="destructive"
                    size="icon"
                    onClick={() => setLines(lines.filter((_, j) => j !== i))}
                  >
                    &times;
                  </Button>
                </div>
              ))}
            </div>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="mt-2"
              onClick={addLine}
            >
              + Regel
            </Button>
          </div>
          <Button type="submit" className="w-full">
            Opslaan
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function OrderDetailDialog({
  order,
  onClose,
  onChanged,
}: {
  order: Order | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  if (!order) return null;

  async function handleDelete() {
    if (!confirm("Order verwijderen?")) return;
    await api.deleteOrder(order!.id);
    toast.success("Order verwijderd");
    onClose();
    onChanged();
  }

  return (
    <Dialog open={!!order} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {order.order_number} — {order.customer_name}
          </DialogTitle>
        </DialogHeader>
        <Badge
          variant={order.status as "pending" | "picking" | "completed"}
          className="mb-3"
        >
          {statusLabel[order.status] || order.status}
        </Badge>
        <div className="space-y-2">
          {order.lines.map((l) => (
            <Card key={l.id} className="p-3">
              <div className="flex justify-between items-center">
                <div>
                  <p className="font-semibold text-sm">{l.sku_name}</p>
                  <p className="text-xs text-muted-foreground">{l.sku_code}</p>
                </div>
                <Badge
                  variant={l.status as "pending" | "completed"}
                >
                  {l.picked_quantity}/{l.quantity}
                </Badge>
              </div>
            </Card>
          ))}
        </div>
        <div className="flex gap-2 mt-4">
          <Button variant="destructive" onClick={handleDelete}>
            Verwijder
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
