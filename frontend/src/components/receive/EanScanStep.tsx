import { useState, useRef, useEffect, type FormEvent } from "react";
import { toast } from "@/App";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { EanBookingResult, Order } from "./types";

/**
 * Barcode picking: focus an input, let the handscanner type the EAN + Enter,
 * book one unit, refocus for the next scan. No camera, no AI — the handscanner
 * counterpart to the vision ScanStep, chosen per order by its pick_method.
 */
export function EanScanStep({ order, onBack }: { order: Order; onBack: () => void }) {
  const [ean, setEan] = useState("");
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<EanBookingResult[]>([]);
  const [completed, setCompleted] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Keep the input focused so a handscanner always lands its keystrokes here.
  useEffect(() => {
    inputRef.current?.focus();
  }, [results, busy]);

  const bookedThisSession = results.reduce((sum, r) => sum + r.booked_quantity, 0);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const code = ean.trim();
    if (!code || busy) return;
    setBusy(true);
    try {
      const result: EanBookingResult = await api.scanEan(order.id, code);
      setResults((prev) => [result, ...prev]);
      setEan("");
      if (result.order_completed) {
        setCompleted(true);
        toast.success("Order compleet");
      }
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? err.message : "Scanfout";
      toast.error(msg);
      setEan("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Card className="p-3 mb-3">
        <p className="text-sm font-semibold">{order.reference}</p>
        <p className="text-xs text-muted-foreground">
          {order.customer_name ?? "—"} · {bookedThisSession} gescand deze sessie
        </p>
      </Card>

      {completed ? (
        <Card className="p-4 mb-3 bg-emerald-50 border-emerald-200">
          <p className="text-sm font-semibold text-emerald-800">Order compleet</p>
          <p className="text-xs text-emerald-700">
            Alles op deze order is gescand en geboekt.
          </p>
        </Card>
      ) : (
        <form onSubmit={handleSubmit} className="mb-3">
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
            disabled={busy}
            placeholder="Scan of typ de EAN…"
            className="w-full h-14 text-lg font-mono px-4 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-ring"
          />
          <Button type="submit" size="lg" className="w-full text-lg h-14 mt-3" disabled={busy || !ean.trim()}>
            {busy ? "Boeken…" : "Boek"}
          </Button>
        </form>
      )}

      {results.length > 0 && (
        <div className="space-y-2 mb-3">
          {results.map((r, i) => (
            <Card key={`${r.order_line_id}-${i}`} className="p-3">
              <div className="flex justify-between items-center">
                <p className="text-sm font-medium truncate" title={r.sku_name}>
                  ✓ {r.sku_name}
                </p>
                <span className="text-xs text-muted-foreground shrink-0 ml-2">
                  nog {r.remaining_quantity}
                </span>
              </div>
              <p className="text-xs text-muted-foreground font-mono">
                {r.sku_code} · {r.rolcontainer}
              </p>
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
