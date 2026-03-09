import { useState, useEffect, useCallback, useRef } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
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
  booked_count: number;
  has_image: boolean;
}

interface Order {
  id: number;
  reference: string;
  status: string;
  merchant_name: string;
  created_at: string;
  lines: OrderLine[];
  total_boxes: number;
  booked_boxes: number;
}

interface CSVResult {
  matched_skus: { id: number; sku_code: string; name: string; image_count: number }[];
  new_skus: { id: number; sku_code: string; name: string; image_count: number }[];
  errors: string[];
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

export function OrdersPage() {
  const { user } = useAuth();
  const [orders, setOrders] = useState<Order[]>([]);
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);
  const [showUpload, setShowUpload] = useState(false);
  const [csvResult, setCsvResult] = useState<CSVResult | null>(null);

  const load = useCallback(async () => {
    try {
      setOrders(await api.listOrders());
    } catch {
      toast.error("Kan orders niet laden");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Orders</h2>
        {user && user.role !== "courier" && (
          <Button size="sm" onClick={() => setShowUpload(true)}>
            + CSV Upload
          </Button>
        )}
      </div>

      <div className="space-y-3">
        {orders.length === 0 ? (
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
                {o.merchant_name} &middot; {o.lines.length} product
                {o.lines.length !== 1 ? "en" : ""}
              </p>
              <p className="text-sm text-muted-foreground">
                {o.booked_boxes}/{o.total_boxes} dozen geboekt
              </p>
            </Card>
          ))
        )}
      </div>

      <CSVUploadDialog
        open={showUpload}
        onClose={() => {
          setShowUpload(false);
          setCsvResult(null);
        }}
        onResult={(r) => {
          setCsvResult(r);
          load();
        }}
        result={csvResult}
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

function CSVUploadDialog({
  open,
  onClose,
  onResult,
  result,
}: {
  open: boolean;
  onClose: () => void;
  onResult: (r: CSVResult) => void;
  result: CSVResult | null;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const res = await api.uploadCSV(file);
      onResult(res);
      toast.success("CSV verwerkt");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Upload mislukt");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>CSV Importeren</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <Label className="mb-2 block text-sm">
              Upload een CSV met kolommen: producent, wijnaam, type, jaargang,
              volume, aantal (scheidingsteken: puntkomma)
            </Label>
            <Button
              variant="secondary"
              onClick={() => fileRef.current?.click()}
              disabled={uploading}
            >
              {uploading ? "Uploaden..." : "CSV selecteren"}
            </Button>
            <input
              ref={fileRef}
              type="file"
              accept=".csv,.txt"
              className="hidden"
              onChange={handleFile}
            />
          </div>

          {result && (
            <div className="space-y-3">
              {result.errors.length > 0 && (
                <div className="p-3 bg-red-600/20 border border-red-600 rounded-lg">
                  <p className="text-sm font-semibold text-red-400 mb-1">
                    Waarschuwingen
                  </p>
                  {result.errors.map((e, i) => (
                    <p key={i} className="text-xs text-red-300">
                      {e}
                    </p>
                  ))}
                </div>
              )}

              {result.matched_skus.length > 0 && (
                <div>
                  <p className="text-sm font-semibold mb-1">
                    Bestaande SKU's gekoppeld ({result.matched_skus.length})
                  </p>
                  {result.matched_skus.map((s) => (
                    <p key={s.id} className="text-xs text-muted-foreground">
                      {s.sku_code} — {s.name}
                    </p>
                  ))}
                </div>
              )}

              {result.new_skus.length > 0 && (
                <div>
                  <p className="text-sm font-semibold mb-1 text-amber-400">
                    Nieuwe SKU's aangemaakt ({result.new_skus.length}) — upload
                    referentiebeelden
                  </p>
                  {result.new_skus.map((s) => (
                    <div
                      key={s.id}
                      className="flex items-center justify-between text-xs py-1"
                    >
                      <span>
                        {s.sku_code} — {s.name}
                      </span>
                      <Badge variant={s.image_count > 0 ? "active" : "inactive"}>
                        {s.image_count > 0 ? "Beeld" : "Geen beeld"}
                      </Badge>
                    </div>
                  ))}
                  <p className="text-xs text-muted-foreground mt-2">
                    Ga naar Producten om referentiebeelden te uploaden
                  </p>
                </div>
              )}

              {result.new_skus.length === 0 &&
                result.matched_skus.length > 0 &&
                result.errors.length === 0 && (
                  <p className="text-sm text-green-400">
                    Alle SKU's gekoppeld — order is direct actief
                  </p>
                )}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
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
  const [activating, setActivating] = useState(false);

  if (!order) return null;

  const skusWithoutImages = order.lines.filter((l) => !l.has_image);
  const canActivate =
    (order.status === "draft" || order.status === "pending_images") &&
    skusWithoutImages.length === 0;

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
            Handelaar: {order.merchant_name}
          </p>
          <p className="text-sm text-muted-foreground">
            Voortgang: {order.booked_boxes}/{order.total_boxes} dozen geboekt
          </p>

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
                      {line.sku_code}
                    </p>
                  </div>
                  <div className="text-right">
                    <p>
                      {line.booked_count}/{line.quantity} dozen
                    </p>
                    {!line.has_image && (
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
                Upload beelden via Producten om de order te activeren.
              </p>
            </div>
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
        </div>
      </DialogContent>
    </Dialog>
  );
}
