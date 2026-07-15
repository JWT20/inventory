import { useState, useRef, useEffect, type FormEvent } from "react";
import { toast } from "@/App";
import { api, ApiError } from "@/lib/api";
import { fireCompletion } from "@/lib/celebrate";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type {
  EanBookingResult,
  LabelScanResult,
  LocationScanResult,
  Order,
  UndoScanResult,
} from "./types";

/**
 * Barcode picking, full flow: an on-screen picklist of the order's lines, a
 * focused input the handscanner types EAN + Enter into (one scan = one unit),
 * per-scan undo for a wrong/damaged grab, and a final shipping-label gate that
 * verifies the Veloyd label before the order ships.
 *
 * When the order's products have pick locations, the flow is location-driven:
 * the courier scans a shelf, books the EANs there, then walks to the next
 * shelf. The backend enforces that a located product is only booked from its
 * own shelf (hufterproef). Orders without located products skip straight to
 * scanning, unchanged.
 *
 * Errors are blocking: a scan/label/undo failure raises a red panel the courier
 * must acknowledge, and the scan field is not refocused until they do — so a
 * fast handscanner never scrolls past a problem.
 */
type Phase = "location" | "scan" | "label" | "done";

export function EanScanStep({ order, onBack }: { order: Order; onBack: () => void }) {
  const lines = order.lines ?? [];
  const hasLocations = lines.some((l) => l.pick_location);
  // Only channel orders (Shopify) carry a Veloyd shipping label to verify.
  // Manual barcode orders have no label and finish straight after picking.
  const needsLabel = (order.channel ?? "manual") !== "manual";

  const [ean, setEan] = useState("");
  const [label, setLabel] = useState("");
  const [locationCode, setLocationCode] = useState("");
  const [activeLocation, setActiveLocation] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<Phase>(
    // A reopened, already-completed order lands on its final step directly:
    // the label gate for channel orders, or "done" for manual ones.
    order.status === "completed"
      ? needsLabel
        ? "label"
        : "done"
      : hasLocations
        ? "location"
        : "scan",
  );
  const [results, setResults] = useState<EanBookingResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  // booked_count per line, seeded from the order and updated live as scans land.
  const [booked, setBooked] = useState<Record<number, number>>(() =>
    Object.fromEntries(lines.map((l) => [l.id, l.booked_count])),
  );
  const inputRef = useRef<HTMLInputElement>(null);
  const labelInputRef = useRef<HTMLInputElement>(null);
  const locationInputRef = useRef<HTMLInputElement>(null);
  // Fire the completion confetti once per fresh transition into the terminal
  // "done" phase (order picked + shipped). Guarded so it never fires on mount
  // for an already-finished order, and re-arms after an undo reopens the order.
  const celebrated = useRef(false);

  const totalQty = lines.reduce((s, l) => s + l.quantity, 0);
  const totalBooked = lines.reduce(
    (s, l) => s + Math.min(booked[l.id] ?? 0, l.quantity),
    0,
  );

  // Distinct locations that still have open lines — shown as walking hints.
  const openLocations = Array.from(
    new Set(
      lines
        .filter((l) => l.pick_location && (booked[l.id] ?? 0) < l.quantity)
        .map((l) => l.pick_location as string),
    ),
  ).sort();

  // Picklist sorted by location (a natural route), then product name.
  const sortedLines = [...lines].sort((a, b) => {
    const la = a.pick_location ?? "￿";
    const lb = b.pick_location ?? "￿";
    return la === lb ? a.sku_name.localeCompare(b.sku_name) : la.localeCompare(lb);
  });

  // Keep the active field focused for the handscanner — but never while a
  // blocking error is up: the courier must acknowledge it first.
  useEffect(() => {
    if (error) return;
    if (phase === "location") locationInputRef.current?.focus();
    else if (phase === "scan") inputRef.current?.focus();
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

  async function handleLocation(e: FormEvent) {
    e.preventDefault();
    const code = locationCode.trim();
    if (!code || busy) return;
    setBusy(true);
    try {
      const res: LocationScanResult = await api.scanLocation(order.id, code);
      setActiveLocation(res.location_code);
      setLocationCode("");
      setPhase("scan");
    } catch (err: unknown) {
      setLocationCode("");
      setError(err instanceof ApiError ? err.message : "Locatiefout");
    } finally {
      setBusy(false);
    }
  }

  async function handleScan(e: FormEvent) {
    e.preventDefault();
    const code = ean.trim();
    if (!code || busy) return;
    setBusy(true);
    try {
      const result: EanBookingResult = await api.scanEan(order.id, code, activeLocation);
      const nextBooked = {
        ...booked,
        [result.order_line_id]:
          lineQuantity(result.order_line_id) - result.remaining_quantity,
      };
      setResults((prev) => [result, ...prev]);
      setBooked(nextBooked);
      setEan("");
      if (result.order_completed) {
        if (needsLabel) {
          setPhase("label");
        } else {
          setPhase("done");
          toast.success("Order klaar");
        }
        return;
      }
      // If this shelf is now fully picked, decide where to go next. Only return
      // to the location phase when OTHER located shelves still have open work;
      // if all that remains is unlocated products, stay in the scan phase (they
      // are bookable without a location) — otherwise a mixed order would dead-end
      // in a location phase with no shelf left to scan.
      if (activeLocation) {
        const here = lines.filter((l) => l.pick_location === activeLocation);
        const hereDone = here.every((l) => (nextBooked[l.id] ?? 0) >= l.quantity);
        if (hereDone) {
          const openLocatedElsewhere = lines.some(
            (l) =>
              l.pick_location &&
              l.pick_location !== activeLocation &&
              (nextBooked[l.id] ?? 0) < l.quantity,
          );
          setActiveLocation(null);
          setPhase(openLocatedElsewhere ? "location" : "scan");
        }
      }
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
      if (undo.order_status === "active") {
        setPhase(hasLocations ? "location" : "scan");
        setActiveLocation(null);
      }
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
          {sortedLines.map((l) => {
            const b = Math.min(booked[l.id] ?? 0, l.quantity);
            const done = b >= l.quantity;
            const isActive = !!activeLocation && l.pick_location === activeLocation;
            return (
              <Card
                key={l.id}
                className={`p-3 ${done ? "bg-emerald-50 border-emerald-200" : isActive ? "border-blue-400" : ""}`}
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
                <p className="text-xs text-muted-foreground font-mono">
                  {l.sku_code}
                  {l.pick_location ? ` · 📍 ${l.pick_location}` : ""}
                </p>
              </Card>
            );
          })}
        </div>
      )}

      {phase === "location" && (
        <Card className="p-4 mb-3 bg-blue-50 border-blue-200">
          <p className="text-sm font-semibold text-blue-900 mb-1">
            Scan de locatie
          </p>
          <p className="text-xs text-blue-800 mb-3">
            {openLocations.length > 0
              ? `Nog te doen: ${openLocations.join(", ")}`
              : "Scan de plank waar je staat."}
          </p>
          <form onSubmit={handleLocation}>
            <input
              ref={locationInputRef}
              value={locationCode}
              onChange={(e) => setLocationCode(e.target.value)}
              autoComplete="off"
              autoFocus
              disabled={busy || !!error}
              placeholder="Scan de locatiecode…"
              className="w-full h-14 text-lg font-mono px-4 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <Button
              type="submit"
              size="lg"
              className="w-full text-lg h-14 mt-3"
              disabled={busy || !!error || !locationCode.trim()}
            >
              {busy ? "Controleren…" : "Locatie bevestigen"}
            </Button>
          </form>
        </Card>
      )}

      {phase === "scan" && (
        <form onSubmit={handleScan} className="mb-3">
          <label className="text-sm text-muted-foreground mb-2 block">
            {activeLocation ? `Scan de barcode (locatie ${activeLocation})` : "Scan de barcode (EAN)"}
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
          {openLocations.length > 0 && (
            <button
              type="button"
              onClick={() => {
                setActiveLocation(null);
                setPhase("location");
              }}
              className="text-sm text-blue-600 underline w-full text-center block mt-3"
            >
              Andere locatie scannen
            </button>
          )}
        </form>
      )}

      {phase === "label" && (
        <Card className="p-4 mb-3 bg-amber-50 border-amber-200">
          <p className="text-sm font-semibold text-amber-900 mb-1">
            Order compleet — inpakken en scan verzendlabel
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
          <p className="text-sm font-semibold text-emerald-800">
            {needsLabel ? "Verzendklaar" : "Order klaar"}
          </p>
          <p className="text-xs text-emerald-700">
            {needsLabel
              ? "Label gecontroleerd en order verzonden."
              : "Alles gepickt — order afgerond."}
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
