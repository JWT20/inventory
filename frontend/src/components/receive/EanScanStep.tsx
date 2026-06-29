import { useState, useRef, useEffect, type FormEvent } from "react";
import { toast } from "@/App";
import { api, ApiError } from "@/lib/api";
import { fireCompletion } from "@/lib/celebrate";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type {
  EanBookingResult,
  LabelScanResult,
  Order,
  UndoScanResult,
} from "./types";

/**
 * Barcode picking, full flow: an on-screen picklist of the order's lines, a
 * focused input the handscanner types EAN + Enter into (one scan = one unit),
 * per-scan undo for a wrong/damaged grab, and a final shipping-label gate that
 * verifies the Veloyd label before the order ships.
 *
 * Errors are blocking: a scan/label/undo failure raises a red panel the courier
 * must acknowledge, and the scan field is not refocused until they do — so a
 * fast handscanner never scrolls past a problem.
 */
type Phase = "scan" | "label" | "done";

export function EanScanStep({ order, onBack }: { order: Order; onBack: () => void }) {
  const [ean, setEan] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<Phase>("scan");
  const [results, setResults] = useState<EanBookingResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const lines = order.lines ?? [];
  // booked_count per line, seeded from the order and updated live as scans land.
  const [booked, setBooked] = useState<Record<number, number>>(() =>
    Object.fromEntries(lines.map((l) => [l.id, l.booked_count])),
  );
  const inputRef = useRef<HTMLInputElement>(null);
  const labelInputRef = useRef<HTMLInputElement>(null);
  // Fire the completion confetti once per fresh transition into the terminal
  // "done" phase (order picked + shipped). Guarded so it never fires on mount
  // for an already-finished order, and re-arms after an undo reopens the order.
  const celebrated = useRef(false);

  const totalQty = lines.reduce((s, l) => s + l.quantity, 0);
  const totalBooked = lines.reduce(
    (s, l) => s + Math.min(booked[l.id] ?? 0, l.quantity),
    0,
  );

  // Keep the active scan field focused for the handscanner — but never while a
  // blocking error is up: the courier must acknowledge it first.
  useEffect(() => {
    if (error) return;
    if (phase === "scan") inputRef.current?.focus();
    else if (phase === "label") labelInputRef.current?.focus();
  }, [results, busy, phase, error]);

  useEffect(() => {
    if (phase === "done") {
      if (!celebrated.current) {
        celebrated.current = true;
        fireCompletion();
      }
    } else {
      celebrated.current = false;
    }
  }, [phase]);

  function lineQuantity(lineId: number): number {
    return lines.find((l) => l.id === lineId)?.quantity ?? 0;
  }

  async function handleScan(e: FormEvent) {
    e.preventDefault();
    const code = ean.trim();
    if (!code || busy) return;
    setBusy(true);
    try {
      const result: EanBookingResult = await api.scanEan(order.id, code);
      setResults((prev) => [result, ...prev]);
      setBooked((prev) => ({
        ...prev,
        [result.order_line_id]:
          lineQuantity(result.order_line_id) - result.remaining_quantity,
      }));
      setEan("");
      if (result.order_completed) setPhase("label");
    } catch (err: unknown) {
      setEan("");
      setError(err instanceof ApiError ? err.message : "Scanfout");
    } finally {
      setBusy(false);
    }
  }

  async function handleUndo(result: EanBookingResult) {
    if (busy) return;
    setBusy(true);
    try {
      const undo: UndoScanResult = await api.undoScan(result.booking_id);
      setResults((prev) => prev.filter((r) => r.booking_id !== result.booking_id));
      setBooked((prev) => ({
        ...prev,
        [undo.order_line_id]:
          lineQuantity(undo.order_line_id) - undo.remaining_quantity,
      }));
      // Undoing the unit that completed the order reopens it for scanning.
      if (undo.order_status === "active") setPhase("scan");
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.message : "Kon niet terugdraaien");
    } finally {
      setBusy(false);
    }
  }

  async function handleLabel(e: FormEvent) {
    e.preventDefault();
    const code = label.trim();
    if (!code || busy) return;
    setBusy(true);
    try {
      const res: LabelScanResult = await api.scanLabel(order.id, code);
      setLabel("");
      if (res.status === "shipped") {
        setPhase("done");
        toast.success("Verzendklaar");
      }
    } catch (err: unknown) {
      setLabel("");
      setError(err instanceof ApiError ? err.message : "Labelfout");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {error && (
        <Card className="p-4 mb-3 bg-red-50 border-red-300">
          <p className="text-sm font-semibold text-red-800">Fout</p>
          <p className="text-sm text-red-700 mb-3">{error}</p>
          <Button
            size="lg"
            variant="destructive"
            className="w-full"
            onClick={() => setError(null)}
          >
            Verder
          </Button>
        </Card>
      )}

      <Card className="p-3 mb-3">
        <p className="text-sm font-semibold">{order.reference}</p>
        <p className="text-xs text-muted-foreground">
          {order.customer_name ?? "—"} · {totalBooked}/{totalQty} gescand
        </p>
      </Card>

      {lines.length > 0 && (
        <div className="space-y-2 mb-3">
          {lines.map((l) => {
            const b = Math.min(booked[l.id] ?? 0, l.quantity);
            const done = b >= l.quantity;
            return (
              <Card
                key={l.id}
                className={`p-3 ${done ? "bg-emerald-50 border-emerald-200" : ""}`}
              >
                <div className="flex justify-between items-center">
                  <p
                    className={`text-sm font-medium truncate ${done ? "text-emerald-800" : ""}`}
                    title={l.sku_name}
                  >
                    {done ? "✓ " : ""}
                    {l.sku_name}
                  </p>
                  <span className="text-xs font-mono shrink-0 ml-2">
                    {b}/{l.quantity}
                  </span>
                </div>
                <p className="text-xs text-muted-foreground font-mono">{l.sku_code}</p>
              </Card>
            );
          })}
        </div>
      )}

      {phase === "scan" && (
        <form onSubmit={handleScan} className="mb-3">
          <label className="text-sm text-muted-foreground mb-2 block">
            Scan de barcode (EAN)
          </label>
          <input
            ref={inputRef}
            value={ean}
            onChange={(e) => setEan(e.target.value)}
            inputMode="numeric"
            autoComplete="off"
            autoFocus
            disabled={busy || !!error}
            placeholder="Scan of typ de EAN…"
            className="w-full h-14 text-lg font-mono px-4 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-ring"
          />
          <Button
            type="submit"
            size="lg"
            className="w-full text-lg h-14 mt-3"
            disabled={busy || !!error || !ean.trim()}
          >
            {busy ? "Boeken…" : "Boek"}
          </Button>
        </form>
      )}

      {phase === "label" && (
        <Card className="p-4 mb-3 bg-amber-50 border-amber-200">
          <p className="text-sm font-semibold text-amber-900 mb-1">
            Order compleet — scan verzendlabel
          </p>
          <p className="text-xs text-amber-800 mb-3">
            Scan het Veloyd-label om de order te verzenden.
          </p>
          <form onSubmit={handleLabel}>
            <input
              ref={labelInputRef}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              autoComplete="off"
              autoFocus
              disabled={busy || !!error}
              placeholder="Scan het verzendlabel…"
              className="w-full h-14 text-lg font-mono px-4 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <Button
              type="submit"
              size="lg"
              className="w-full text-lg h-14 mt-3"
              disabled={busy || !!error || !label.trim()}
            >
              {busy ? "Controleren…" : "Verzenden"}
            </Button>
          </form>
        </Card>
      )}

      {phase === "done" && (
        <Card className="p-4 mb-3 bg-emerald-50 border-emerald-200">
          <p className="text-sm font-semibold text-emerald-800">Verzendklaar</p>
          <p className="text-xs text-emerald-700">
            Label gecontroleerd en order verzonden.
          </p>
        </Card>
      )}

      {results.length > 0 && phase !== "done" && (
        <div className="space-y-2 mb-3">
          <p className="text-xs text-muted-foreground">Deze sessie gescand</p>
          {results.map((r) => (
            <Card key={r.booking_id} className="p-3 flex justify-between items-center">
              <p className="text-sm font-medium truncate" title={r.sku_name}>
                ✓ {r.sku_name}
              </p>
              <button
                onClick={() => handleUndo(r)}
                disabled={busy}
                className="text-xs text-red-600 underline shrink-0 ml-2 disabled:opacity-50"
              >
                Ongedaan
              </button>
            </Card>
          ))}
        </div>
      )}

      <button
        onClick={onBack}
        className="text-sm text-muted-foreground underline w-full text-center block mt-3"
      >
        Terug naar orders
      </button>
    </>
  );
}
