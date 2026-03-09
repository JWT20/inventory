import { useState, useEffect, useCallback, useRef } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
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
  scanned_quantity: number;
}

interface Order {
  id: number;
  order_number: string;
  customer_name: string;
  status: string;
  lines: OrderLine[];
}

interface SKUInfo {
  id: number;
  sku_code: string;
  name: string;
  image_count: number;
}

interface ScanResult {
  matched: boolean;
  sku_code: string | null;
  sku_name: string | null;
  confidence: number;
  order_line_id: number | null;
  scanned_quantity: number;
  total_quantity: number;
  customer_name: string;
  message: string;
}

const statusLabel: Record<string, string> = {
  draft: "Concept",
  active: "Actief",
  completed: "Klaar",
};

const statusVariant: Record<string, string> = {
  draft: "inactive",
  active: "active",
  completed: "completed",
};

export function OrdersPage() {
  const { user } = useAuth();
  const [orders, setOrders] = useState<Order[]>([]);
  const [showImport, setShowImport] = useState(false);
  const [detail, setDetail] = useState<Order | null>(null);
  const [scanning, setScanning] = useState<Order | null>(null);

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

  if (scanning) {
    return (
      <ScanView
        order={scanning}
        onStop={() => {
          setScanning(null);
          load();
        }}
      />
    );
  }

  return (
    <>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Orders</h2>
        {user && user.role !== "courier" && (
          <Button size="sm" onClick={() => setShowImport(true)}>
            + Importeren
          </Button>
        )}
      </div>

      <div className="space-y-3">
        {orders.length === 0 ? (
          <p className="text-center text-muted-foreground py-10">
            Geen orders gevonden
          </p>
        ) : (
          orders.map((o) => {
            const total = o.lines.reduce((s, l) => s + l.quantity, 0);
            const scanned = o.lines.reduce((s, l) => s + l.scanned_quantity, 0);
            return (
              <Card
                key={o.id}
                className="p-4 cursor-pointer active:scale-[0.98] transition-transform"
                onClick={() => setDetail(o)}
              >
                <div className="flex justify-between items-center mb-1">
                  <span className="font-semibold">{o.order_number}</span>
                  <Badge variant={statusVariant[o.status] as "active" | "inactive"}>
                    {statusLabel[o.status] || o.status}
                  </Badge>
                </div>
                <p className="text-sm text-muted-foreground">
                  {o.customer_name}
                </p>
                <p className="text-sm text-muted-foreground mt-1">
                  {scanned}/{total} dozen gescand &bull; {o.lines.length} product
                  {o.lines.length !== 1 ? "en" : ""}
                </p>
              </Card>
            );
          })
        )}
      </div>

      <ImportDialog
        open={showImport}
        onClose={() => setShowImport(false)}
        onImported={load}
      />
      {detail && (
        <OrderDetailDialog
          order={detail}
          onClose={() => {
            setDetail(null);
            load();
          }}
          onScan={(o) => {
            setDetail(null);
            setScanning(o);
          }}
        />
      )}
    </>
  );
}

/* ---------- Import Dialog ---------- */

function ImportDialog({
  open,
  onClose,
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  onImported: () => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [orderNumber, setOrderNumber] = useState("");
  const [customerName, setCustomerName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{
    order: Order;
    new_skus: SKUInfo[];
    existing_skus: SKUInfo[];
  } | null>(null);

  useEffect(() => {
    if (open) {
      setFile(null);
      setOrderNumber("");
      setCustomerName("");
      setResult(null);
    }
  }, [open]);

  async function handleImport(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setSubmitting(true);
    try {
      const res = await api.importOrder(file, orderNumber, customerName);
      setResult(res);
      toast.success(`Order ${res.order.order_number} geimporteerd`);
      onImported();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Import mislukt");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Order Importeren</DialogTitle>
        </DialogHeader>

        {!result ? (
          <form onSubmit={handleImport} className="space-y-4">
            <div className="space-y-2">
              <Label>Ordernummer (optioneel)</Label>
              <Input
                value={orderNumber}
                onChange={(e) => setOrderNumber(e.target.value)}
                placeholder="Wordt automatisch gegenereerd"
              />
            </div>
            <div className="space-y-2">
              <Label>Klantnaam</Label>
              <Input
                value={customerName}
                onChange={(e) => setCustomerName(e.target.value)}
                placeholder="Klantnaam"
                required
              />
            </div>
            <div className="space-y-2">
              <Label>CSV of Excel bestand</Label>
              <p className="text-xs text-muted-foreground">
                Kolommen: producent, wijnnaam, type, jaargang, volume, aantal
              </p>
              <Button
                type="button"
                variant="secondary"
                onClick={() => fileInputRef.current?.click()}
              >
                {file ? file.name : "Bestand kiezen"}
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,.xlsx"
                className="hidden"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
            </div>
            <Button type="submit" className="w-full" disabled={!file || submitting}>
              {submitting ? "Importeren..." : "Importeren"}
            </Button>
          </form>
        ) : (
          <div className="space-y-4">
            <div className="p-3 rounded-lg bg-green-600/20 border border-green-600 text-green-500 text-sm">
              Order <strong>{result.order.order_number}</strong> aangemaakt met{" "}
              {result.order.lines.length} regel(s).
            </div>

            {result.new_skus.length > 0 && (
              <div>
                <p className="text-sm font-semibold mb-2">
                  Nieuwe SKU's — upload referentiebeelden:
                </p>
                <div className="space-y-2">
                  {result.new_skus.map((s) => (
                    <SkuImageUploadRow key={s.id} sku={s} />
                  ))}
                </div>
              </div>
            )}

            {result.existing_skus.length > 0 && (
              <div>
                <p className="text-sm font-semibold mb-2 text-muted-foreground">
                  Bestaande SKU's (al gekoppeld):
                </p>
                <div className="space-y-1">
                  {result.existing_skus.map((s) => (
                    <p key={s.id} className="text-sm text-muted-foreground">
                      {s.name} — {s.image_count} beeld(en)
                    </p>
                  ))}
                </div>
              </div>
            )}

            <Button className="w-full" onClick={onClose}>
              Sluiten
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

/* ---------- SKU Image Upload Row ---------- */

function SkuImageUploadRow({ sku }: { sku: SKUInfo }) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploaded, setUploaded] = useState(false);

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      await api.uploadImage(sku.id, file);
      setUploaded(true);
      toast.success(`Referentiebeeld voor ${sku.name} toegevoegd`);
    } catch {
      toast.error(`Upload mislukt voor ${sku.name}`);
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="flex items-center justify-between gap-2 p-2 border border-border rounded-lg">
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium truncate">{sku.name}</p>
        <p className="text-xs text-muted-foreground">{sku.sku_code}</p>
      </div>
      {uploaded ? (
        <Badge variant="active">Geupload</Badge>
      ) : (
        <>
          <Button
            size="sm"
            variant="secondary"
            disabled={uploading}
            onClick={() => fileInputRef.current?.click()}
          >
            {uploading ? "Uploaden..." : "Foto"}
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            capture="environment"
            className="hidden"
            onChange={handleFile}
          />
        </>
      )}
    </div>
  );
}

/* ---------- Order Detail Dialog ---------- */

function OrderDetailDialog({
  order: initialOrder,
  onClose,
  onScan,
}: {
  order: Order;
  onClose: () => void;
  onScan: (order: Order) => void;
}) {
  const { user } = useAuth();
  const [order, setOrder] = useState(initialOrder);
  const [activating, setActivating] = useState(false);

  async function refresh() {
    try {
      setOrder(await api.getOrder(order.id));
    } catch { /* ignore */ }
  }

  async function handleActivate() {
    setActivating(true);
    try {
      const updated = await api.activateOrder(order.id);
      setOrder(updated);
      toast.success("Order geactiveerd — klaar om te scannen");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Kan niet activeren");
    } finally {
      setActivating(false);
    }
  }

  async function handleDelete() {
    if (!confirm("Order verwijderen?")) return;
    try {
      await api.deleteOrder(order.id);
      toast.success("Order verwijderd");
      onClose();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Kan niet verwijderen");
    }
  }

  const total = order.lines.reduce((s, l) => s + l.quantity, 0);
  const scanned = order.lines.reduce((s, l) => s + l.scanned_quantity, 0);

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {order.order_number} — {order.customer_name}
          </DialogTitle>
        </DialogHeader>

        <div className="flex items-center gap-2 mb-3">
          <Badge variant={statusVariant[order.status] as "active" | "inactive"}>
            {statusLabel[order.status] || order.status}
          </Badge>
          <span className="text-sm text-muted-foreground">
            {scanned}/{total} dozen gescand
          </span>
        </div>

        <div className="space-y-2 mb-4">
          {order.lines.map((l) => {
            const done = l.scanned_quantity >= l.quantity;
            return (
              <Card key={l.id} className="p-3">
                <div className="flex justify-between items-center">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold truncate">{l.sku_name}</p>
                    <p className="text-xs text-muted-foreground">{l.sku_code}</p>
                  </div>
                  <Badge variant={done ? "active" : "inactive"}>
                    {l.scanned_quantity}/{l.quantity}
                  </Badge>
                </div>
              </Card>
            );
          })}
        </div>

        <div className="flex flex-col gap-2">
          {order.status === "active" && (
            <Button
              size="lg"
              className="w-full h-14 text-lg"
              onClick={() => onScan(order)}
            >
              Scannen
            </Button>
          )}
          {order.status === "draft" && user && user.role !== "courier" && (
            <Button
              className="w-full"
              onClick={handleActivate}
              disabled={activating}
            >
              {activating ? "Activeren..." : "Order activeren"}
            </Button>
          )}
          {order.status !== "completed" && user && user.role !== "courier" && (
            <Button variant="destructive" size="sm" onClick={handleDelete}>
              Verwijderen
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/* ---------- Scan View (full-screen) ---------- */

function ScanView({
  order: initialOrder,
  onStop,
}: {
  order: Order;
  onStop: () => void;
}) {
  const [order, setOrder] = useState(initialOrder);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [scanning, setScanning] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const total = order.lines.reduce((s, l) => s + l.quantity, 0);
  const scanned = order.lines.reduce((s, l) => s + l.scanned_quantity, 0);

  useEffect(() => {
    async function startCamera() {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: { ideal: "environment" },
            width: { ideal: 1280 },
            height: { ideal: 960 },
          },
        });
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play();
        }
      } catch {
        toast.error("Camera niet beschikbaar");
      }
    }
    startCamera();
    return () => {
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  async function capture() {
    if (!videoRef.current || !canvasRef.current) return;
    setScanning(true);
    setResult(null);

    const canvas = canvasRef.current;
    canvas.width = videoRef.current.videoWidth;
    canvas.height = videoRef.current.videoHeight;
    canvas.getContext("2d")!.drawImage(videoRef.current, 0, 0);

    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.85),
    );
    if (!blob) {
      setScanning(false);
      return;
    }

    try {
      const res: ScanResult = await api.scanOrder(order.id, blob);
      setResult(res);

      // Refresh order to update scanned counts
      const updated = await api.getOrder(order.id);
      setOrder(updated);

      if (updated.status === "completed") {
        toast.success("Order compleet!");
        setTimeout(onStop, 2000);
      }
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Scanfout");
    } finally {
      setScanning(false);
    }
  }

  return (
    <div>
      <div className="mb-3">
        <div className="flex justify-between items-center">
          <p className="text-sm font-semibold">
            {order.order_number} — {order.customer_name}
          </p>
          <Badge variant="active">{scanned}/{total} dozen</Badge>
        </div>
      </div>

      <div className="relative w-full aspect-[4/3] rounded-lg overflow-hidden bg-black mb-3">
        <video
          ref={videoRef}
          autoPlay
          playsInline
          className="w-full h-full object-cover"
        />
        <canvas ref={canvasRef} className="hidden" />
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="w-[70%] h-[70%] border-[3px] border-white/50 rounded-2xl" />
        </div>
      </div>

      {result && (
        <div
          className={`p-4 rounded-lg text-center font-semibold mb-3 ${
            result.matched
              ? "bg-green-600/20 border-2 border-green-600 text-green-500"
              : "bg-red-600/20 border-2 border-red-600 text-red-500"
          }`}
        >
          <p>{result.message}</p>
          {result.matched && result.sku_name && (
            <p className="text-sm font-normal mt-1 opacity-80">
              {result.sku_name} — {result.scanned_quantity}/{result.total_quantity} dozen
            </p>
          )}
        </div>
      )}

      <div className="flex flex-col gap-3">
        <Button
          size="lg"
          className="w-full text-lg h-14"
          onClick={capture}
          disabled={scanning}
        >
          {scanning ? "Herkennen..." : "Scan"}
        </Button>
        <button
          onClick={onStop}
          className="text-sm text-muted-foreground underline"
        >
          Stop scannen
        </button>
      </div>
    </div>
  );
}
