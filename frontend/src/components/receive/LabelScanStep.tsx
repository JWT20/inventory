import { useEffect, useRef, useState, type FormEvent } from "react";
import { Camera } from "lucide-react";
import { toast } from "@/App";
import { api, ApiError } from "@/lib/api";
import { fireCompletion } from "@/lib/celebrate";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { CameraBarcodeScanner } from "./CameraBarcodeScanner";
import { SCAN_MODE_WORD } from "./constants";
import type { LabelScanResult, ScanMode } from "./types";

/**
 * Shipping-label gate for a vision-picked (photo/AI) order, mirroring the
 * barcode flow's own label phase: a channel order (advice-app, Shopify, bol)
 * is only released to "shipped" once its Veloyd label is scanned, whatever
 * picking method identified the bottles inside the box.
 */
export function LabelScanStep({
  orderId,
  orderReference,
  scanMode,
  onNext,
  onDone,
}: {
  orderId: number;
  orderReference: string;
  scanMode: ScanMode;
  onNext: () => void;
  onDone: () => void;
}) {
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shipped, setShipped] = useState(false);
  const [cameraOpen, setCameraOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!error) inputRef.current?.focus();
  }, [error, shipped]);

  useEffect(() => {
    if (shipped) fireCompletion();
  }, [shipped]);

  async function processLabel(rawCode: string) {
    const code = rawCode.trim();
    if (!code || busy) return;
    setBusy(true);
    try {
      const res: LabelScanResult = await api.scanLabel(orderId, code);
      setLabel("");
      if (res.status === "shipped") {
        setShipped(true);
        toast.success("Verzendklaar");
      } else {
        toast.success(`Doos gescand — nog ${res.parcels_total - res.parcels_scanned} te gaan`);
      }
    } catch (err: unknown) {
      setLabel("");
      setError(err instanceof ApiError ? err.message : "Labelfout");
    } finally {
      setBusy(false);
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    void processLabel(label);
  }

  if (shipped) {
    return (
      <Card className="p-4 mb-4 bg-emerald-50 border-emerald-200">
        <p className="text-sm font-semibold text-emerald-800">Verzendklaar</p>
        <p className="text-xs text-emerald-700 mb-4">
          Label gecontroleerd — {orderReference} is verzonden.
        </p>
        <div className="flex flex-col gap-3">
          <Button size="lg" className="w-full h-14 text-lg" onClick={onNext}>
            Volgende {SCAN_MODE_WORD[scanMode]} scannen
          </Button>
          <Button variant="secondary" className="w-full" onClick={onDone}>
            Terug naar orders
          </Button>
        </div>
      </Card>
    );
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

      <Card className="p-4 mb-4 bg-amber-50 border-amber-200">
        <p className="text-sm font-semibold text-amber-900 mb-1">
          Order compleet — inpakken en scan verzendlabel
        </p>
        <p className="text-xs text-amber-800 mb-3">
          Scan het Veloyd-label van {orderReference} om te verzenden.
        </p>
        <form onSubmit={handleSubmit}>
          <input
            ref={inputRef}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            autoComplete="off"
            autoFocus
            disabled={busy || !!error}
            placeholder="Scan het verzendlabel…"
            className="w-full h-14 text-lg font-mono px-4 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-ring"
          />
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-3">
            <Button
              type="submit"
              size="lg"
              className="h-14 text-base"
              disabled={busy || !!error || !label.trim()}
            >
              {busy ? "Controleren…" : "Verzenden"}
            </Button>
            <Button
              type="button"
              size="lg"
              variant="outline"
              className="h-14 text-base"
              disabled={busy || !!error}
              onClick={() => setCameraOpen(true)}
            >
              <Camera className="h-5 w-5 mr-2" />
              Scan met camera
            </Button>
          </div>
        </form>
      </Card>

      {cameraOpen && (
        <CameraBarcodeScanner
          open
          mode="label"
          title="Veloyd-label scannen"
          onClose={() => setCameraOpen(false)}
          onScan={(code) => {
            setCameraOpen(false);
            void processLabel(code);
          }}
        />
      )}

      <button
        onClick={onDone}
        className="text-sm text-muted-foreground underline w-full text-center block mt-3"
      >
        Terug naar orders
      </button>
    </>
  );
}
