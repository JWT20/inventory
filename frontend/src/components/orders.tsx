import { useState, useEffect, useCallback, useRef } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface WineLine {
  producent: string;
  wijnaam: string;
  wijntype: string;
  jaargang: string;
  volume: string;
  quantity: number;
}

interface UserOption {
  id: number;
  username: string;
  role: string;
}

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
  const [showManual, setShowManual] = useState(false);
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
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" onClick={() => setShowUpload(true)}>
              CSV Upload
            </Button>
            <Button size="sm" onClick={() => setShowManual(true)}>
              + Order
            </Button>
          </div>
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

      <ManualOrderDialog
        open={showManual}
        onClose={() => setShowManual(false)}
        onCreated={load}
      />

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

const EMPTY_LINE: WineLine = {
  producent: "", wijnaam: "", wijntype: "", jaargang: "", volume: "", quantity: 1,
};

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
  const [merchants, setMerchants] = useState<UserOption[]>([]);
  const [merchantId, setMerchantId] = useState<number | "">("");
  const [lines, setLines] = useState<WineLine[]>([{ ...EMPTY_LINE }]);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    // Auto-set merchant to current user if they're a merchant
    if (user?.role === "merchant") {
      setMerchantId(user.id);
    }
    api.listUsers().then((users: UserOption[]) =>
      setMerchants(users.filter((u) => u.role === "merchant" || u.role === "admin")),
    );
  }, [open, user]);

  function addLine() {
    setLines([...lines, { ...EMPTY_LINE }]);
  }

  function removeLine(idx: number) {
    setLines(lines.filter((_, i) => i !== idx));
  }

  function updateLine(idx: number, field: keyof WineLine, value: string | number) {
    const updated = [...lines];
    updated[idx] = { ...updated[idx], [field]: value };
    setLines(updated);
  }

  async function submit() {
    if (!merchantId) {
      toast.error("Selecteer een handelaar");
      return;
    }
    const valid = lines.filter(
      (l) => l.producent && l.wijnaam && l.wijntype && l.jaargang && l.volume && l.quantity > 0,
    );
    if (valid.length === 0) {
      toast.error("Vul minimaal één complete regel in");
      return;
    }
    setSubmitting(true);
    try {
      await api.createOrder({
        merchant_id: merchantId as number,
        lines: valid,
      });
      toast.success("Order aangemaakt");
      onCreated();
      onClose();
      setMerchantId(user?.role === "merchant" ? user.id : "");
      setLines([{ ...EMPTY_LINE }]);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Aanmaken mislukt");
    } finally {
      setSubmitting(false);
    }
  }

  const inp = "rounded-md border border-border bg-background px-2 py-1.5 text-sm w-full";

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Order aanmaken</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          {user?.role !== "merchant" && (
            <div>
              <Label className="mb-1 block text-sm">Handelaar</Label>
              <select
                className={inp}
                value={merchantId}
                onChange={(e) =>
                  setMerchantId(e.target.value ? Number(e.target.value) : "")
                }
              >
                <option value="">Selecteer handelaar...</option>
                {merchants.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.username}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div>
            <Label className="mb-1 block text-sm">Producten</Label>
            <div className="space-y-3">
              {lines.map((line, idx) => (
                <div
                  key={idx}
                  className="p-3 rounded-lg border border-border space-y-2"
                >
                  <div className="flex justify-between items-center">
                    <span className="text-xs text-muted-foreground">
                      Regel {idx + 1}
                    </span>
                    {lines.length > 1 && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 px-2 text-xs"
                        onClick={() => removeLine(idx)}
                      >
                        ×
                      </Button>
                    )}
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      className={inp}
                      placeholder="Producent"
                      value={line.producent}
                      onChange={(e) => updateLine(idx, "producent", e.target.value)}
                    />
                    <input
                      className={inp}
                      placeholder="Wijnaam"
                      value={line.wijnaam}
                      onChange={(e) => updateLine(idx, "wijnaam", e.target.value)}
                    />
                  </div>
                  <div className="grid grid-cols-4 gap-2">
                    <input
                      className={inp}
                      placeholder="Type"
                      value={line.wijntype}
                      onChange={(e) => updateLine(idx, "wijntype", e.target.value)}
                    />
                    <input
                      className={inp}
                      placeholder="Jaargang"
                      value={line.jaargang}
                      onChange={(e) => updateLine(idx, "jaargang", e.target.value)}
                    />
                    <input
                      className={inp}
                      placeholder="Volume"
                      value={line.volume}
                      onChange={(e) => updateLine(idx, "volume", e.target.value)}
                    />
                    <Input
                      type="number"
                      min={1}
                      placeholder="Aantal"
                      value={line.quantity}
                      onChange={(e) =>
                        updateLine(idx, "quantity", Number(e.target.value) || 1)
                      }
                    />
                  </div>
                </div>
              ))}
            </div>
            <Button
              variant="secondary"
              size="sm"
              className="mt-2"
              onClick={addLine}
            >
              + Regel
            </Button>
          </div>

          <Button
            className="w-full"
            onClick={submit}
            disabled={submitting}
          >
            {submitting ? "Aanmaken..." : "Order aanmaken"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
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
  const { user } = useAuth();
  const [activating, setActivating] = useState(false);
  const [uploadingSkuId, setUploadingSkuId] = useState<number | null>(null);
  const fileRefs = useRef<Record<number, HTMLInputElement | null>>({});

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

  async function handleImageUpload(skuId: number, e: React.ChangeEvent<HTMLInputElement>) {
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
                  <div className="text-right flex items-center gap-2">
                    <p>
                      {line.booked_count}/{line.quantity} dozen
                    </p>
                    {!line.has_image && user && user.role !== "courier" && (
                      <>
                        <Button
                          variant="secondary"
                          size="sm"
                          className="text-xs h-7 px-2"
                          disabled={uploadingSkuId === line.sku_id}
                          onClick={() => fileRefs.current[line.sku_id]?.click()}
                        >
                          {uploadingSkuId === line.sku_id ? "Uploaden..." : "Foto"}
                        </Button>
                        <input
                          ref={(el) => { fileRefs.current[line.sku_id] = el; }}
                          type="file"
                          accept="image/*"
                          capture="environment"
                          className="hidden"
                          onChange={(e) => handleImageUpload(line.sku_id, e)}
                        />
                      </>
                    )}
                    {!line.has_image && (user?.role === "courier" || !user) && (
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
