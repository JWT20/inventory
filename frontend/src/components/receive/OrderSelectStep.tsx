import { useState, useEffect, useRef, type FormEvent } from "react";
import { Camera } from "lucide-react";
import { toast } from "@/App";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { OrderCard } from "./OrderCard";
import { CameraBarcodeScanner } from "./CameraBarcodeScanner";
import { getISOWeek, shiftWeek } from "./week";
import type { LabelOrderOpenResult, Order } from "./types";

export function OrderSelectStep({
  onSelect,
  onThisWeek,
}: {
  onSelect: (order: Order) => void;
  onThisWeek: (week: string) => void;
}) {
  const { user } = useAuth();
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [week, setWeek] = useState(() => getISOWeek(new Date()));
  const [label, setLabel] = useState("");
  const [labelEntryOpen, setLabelEntryOpen] = useState(false);
  const [labelBusy, setLabelBusy] = useState(false);
  const [labelError, setLabelError] = useState<string | null>(null);
  const [cameraOpen, setCameraOpen] = useState(false);
  const labelInputRef = useRef<HTMLInputElement>(null);
  const canUseCourierActions =
    user?.role === "courier" || user?.is_platform_admin;

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const all = await api.listOrders(week);
        // Active orders to pick, plus fully-picked barcode channel orders that
        // still need their shipping label scanned ("Te verzenden") — so the
        // label step survives a refresh or navigating away.
        const worklist = all.filter(
          (o: Order) =>
            o.status === "active" ||
            (o.status === "completed" &&
              o.pick_method === "barcode" &&
              (o.channel ?? "manual") !== "manual"),
        );
        worklist.sort((a: Order, b: Order) => {
          // "Te verzenden" (completed) first — they are one step from done.
          const shipA = a.status === "completed" ? 0 : 1;
          const shipB = b.status === "completed" ? 0 : 1;
          if (shipA !== shipB) return shipA - shipB;
          // Then active orders by booked percentage ascending (least progress first).
          const totalA = a.total_boxes + a.total_bottles;
          const totalB = b.total_boxes + b.total_bottles;
          const pctA = totalA > 0 ? (a.booked_boxes + a.booked_bottles) / totalA : 0;
          const pctB = totalB > 0 ? (b.booked_boxes + b.booked_bottles) / totalB : 0;
          return pctA - pctB;
        });
        setOrders(worklist);
      } catch {
        toast.error("Kan orders niet laden");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [week]);

  useEffect(() => {
    if (labelEntryOpen && !labelBusy && !labelError && !cameraOpen) {
      labelInputRef.current?.focus();
    }
  }, [labelEntryOpen, labelBusy, labelError, cameraOpen]);

  async function openOrderWithLabel(rawCode: string) {
    const code = rawCode.trim();
    if (!code || labelBusy) return;
    setLabelBusy(true);
    try {
      const resolved: LabelOrderOpenResult = await api.openOrderByLabel(code);
      const order: Order = await api.getOrder(resolved.order_id);
      setLabel("");
      onSelect(order);
    } catch (err: unknown) {
      setLabel("");
      setLabelError(err instanceof ApiError ? err.message : "Kon order niet openen");
    } finally {
      setLabelBusy(false);
    }
  }

  function handleLabelScan(e: FormEvent) {
    e.preventDefault();
    void openOrderWithLabel(label);
  }

  function openLabelEntry() {
    setLabelEntryOpen(true);
    if (labelEntryOpen && !labelError) labelInputRef.current?.focus();
  }

  function closeLabelEntry() {
    setLabelEntryOpen(false);
    setCameraOpen(false);
    setLabel("");
    setLabelError(null);
  }

  return (
    <>
      {/* Week navigation */}
      <div className="flex items-center justify-center gap-2 mb-4">
        <Button variant="outline" size="sm" onClick={() => setWeek((w) => shiftWeek(w, -1))}>
          &larr;
        </Button>
        <span className="text-sm font-medium min-w-[7rem] text-center">{week}</span>
        <Button variant="outline" size="sm" onClick={() => setWeek((w) => shiftWeek(w, 1))}>
          &rarr;
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setWeek(getISOWeek(new Date()))}
          className="ml-2"
        >
          Vandaag
        </Button>
      </div>

      <p className="text-sm text-muted-foreground mb-3">
        Kies een order hieronder.
      </p>

      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i} className="p-4">
              <div className="flex justify-between items-center mb-1">
                <Skeleton className="h-5 w-32" />
                <Skeleton className="h-5 w-14 rounded-full" />
              </div>
              <Skeleton className="h-4 w-44 mt-2" />
              <Skeleton className="h-4 w-36 mt-1" />
            </Card>
          ))}
        </div>
      ) : orders.length === 0 ? (
        <p className="text-center text-muted-foreground py-10">
          Geen actieve orders in {week}
        </p>
      ) : (
        <div className="space-y-3">
          {orders.map((o) => (
            <OrderCard key={o.id} order={o} onSelect={onSelect} />
          ))}
        </div>
      )}

      {canUseCourierActions && (
        <>
          <div className="grid grid-cols-2 gap-2 mt-4">
            <Button
              variant="secondary"
              className="w-full"
              onClick={() => onThisWeek(week)}
            >
              Deze week
            </Button>
            <Button
              type="button"
              variant="secondary"
              className="w-full"
              aria-expanded={labelEntryOpen}
              disabled={labelBusy || !!labelError}
              onClick={openLabelEntry}
            >
              {labelBusy ? "Order zoeken…" : "EAN scannen"}
            </Button>
          </div>

          {labelEntryOpen && (
            <Card className="p-4 mt-3">
              <div className="flex items-start justify-between gap-3 mb-3">
                <p className="text-sm font-semibold">
                  Scan het Veloyd-label van een order
                </p>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="shrink-0 -mt-2 -mr-2"
                  disabled={labelBusy}
                  onClick={closeLabelEntry}
                >
                  Sluiten
                </Button>
              </div>

              {labelError ? (
                <div
                  role="alert"
                  className="rounded-lg border border-red-300 bg-red-50 p-3"
                >
                  <p className="text-sm font-semibold text-red-800">Label niet geopend</p>
                  <p className="text-sm text-red-700 mb-3">{labelError}</p>
                  <Button
                    type="button"
                    size="lg"
                    variant="destructive"
                    className="w-full"
                    onClick={() => setLabelError(null)}
                  >
                    Verder
                  </Button>
                </div>
              ) : (
                <form onSubmit={handleLabelScan} className="relative">
                  <input
                    ref={labelInputRef}
                    value={label}
                    onChange={(e) => setLabel(e.target.value)}
                    autoComplete="off"
                    disabled={labelBusy}
                    placeholder={labelBusy ? "Order zoeken…" : "Scan Veloyd-label…"}
                    className="h-12 w-full rounded-lg border bg-background px-4 pr-14 font-mono text-base shadow-sm focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                  <button
                    type="submit"
                    className="sr-only"
                    disabled={labelBusy || !label.trim()}
                  >
                    Order openen
                  </button>
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    aria-label="Scan Veloyd-label met camera"
                    className="absolute right-1 top-1 h-10 w-10"
                    disabled={labelBusy}
                    onClick={() => setCameraOpen(true)}
                  >
                    <Camera className="h-5 w-5" aria-hidden="true" />
                  </Button>
                </form>
              )}
            </Card>
          )}

          <CameraBarcodeScanner
            open={cameraOpen}
            mode="label"
            title="Veloyd-label scannen"
            onClose={() => setCameraOpen(false)}
            onScan={(code) => {
              setCameraOpen(false);
              void openOrderWithLabel(code);
            }}
          />
        </>
      )}
    </>
  );
}
