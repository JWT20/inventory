import { useState, useEffect, useRef } from "react";
import { toast } from "@/App";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ImageLightbox } from "@/components/image-lightbox";
import { formatBoxesBottles } from "@/lib/units";
import { ScanModeToggle } from "./ScanModeToggle";
import { SCAN_MODE_WORD } from "./constants";
import type { ConfirmationData, NextPick, Order, ScanMode, WeeklyPickPhoto } from "./types";

export function ScanStep({
  order,
  scanMode,
  onScanModeChange,
  onBooked,
  onBack,
}: {
  order: Order;
  scanMode: ScanMode;
  onScanModeChange: (mode: ScanMode) => void;
  onBooked: (booking: ConfirmationData) => void;
  onBack: () => void;
}) {
  const [scanning, setScanning] = useState(false);
  // Frozen frame shown over the live camera while recognition runs, so the
  // courier sees exactly what was captured instead of a still-running camera.
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [nextPick, setNextPick] = useState<NextPick | null>(null);
  const [nextPickLoading, setNextPickLoading] = useState(true);
  const [nextPickFailed, setNextPickFailed] = useState(false);
  const [nextPickRetry, setNextPickRetry] = useState(0);
  const [nextPickLightbox, setNextPickLightbox] = useState(false);
  const [weekPhotos, setWeekPhotos] = useState<WeeklyPickPhoto[]>([]);
  const [openProgress, setOpenProgress] = useState<{
    openBoxes: number;
    openBottles: number;
    openOrders: number;
  } | null>(null);
  const [needsRef, setNeedsRef] = useState<{
    register_token: string;
    scan_image_url: string;
    candidates: { sku_id: number; sku_code: string; sku_name: string; remaining_quantity: number }[];
  } | null>(null);
  const [registering, setRegistering] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadOpenProgress() {
      try {
        // Scanning matches across all open weeks, so count every open order.
        const allOrders: Order[] = order.delivery_week
          ? await api.listOrders()
          : [order];
        const openOrders = allOrders
          .filter((o) => o.status === "active" && o.organization_id === order.organization_id)
          .map((o) => ({
            ...o,
            remainingBoxes: Math.max(o.total_boxes - o.booked_boxes, 0),
            remainingBottles: Math.max(o.total_bottles - o.booked_bottles, 0),
          }))
          .filter((o) => o.remainingBoxes + o.remainingBottles > 0);

        if (!cancelled) {
          setOpenProgress({
            openBoxes: openOrders.reduce((sum, o) => sum + o.remainingBoxes, 0),
            openBottles: openOrders.reduce((sum, o) => sum + o.remainingBottles, 0),
            openOrders: openOrders.length,
          });
        }
      } catch {
        if (!cancelled) {
          const remainingBoxes = Math.max(order.total_boxes - order.booked_boxes, 0);
          const remainingBottles = Math.max(order.total_bottles - order.booked_bottles, 0);
          setOpenProgress({
            openBoxes: remainingBoxes,
            openBottles: remainingBottles,
            openOrders: remainingBoxes + remainingBottles > 0 ? 1 : 0,
          });
        }
      }
    }

    loadOpenProgress();
    return () => {
      cancelled = true;
    };
  }, [order]);

  // Suggestion photo of the next SKU to scan. ScanStep remounts after each
  // "volgende scannen", so this refetches itself and stays fresh.
  useEffect(() => {
    let active = true;
    setNextPickLoading(true);
    setNextPickFailed(false);
    api
      .nextPick(order.id, scanMode)
      .then((res: NextPick | null) => { if (active) setNextPick(res); })
      // On failure we cannot tell whether order.id is still active (it may have
      // just been completed), so we never blind-scan against it — block with a
      // retry instead of risking a 400 on a completed context order.
      .catch(() => { if (active) { setNextPick(null); setNextPickFailed(true); } })
      .finally(() => { if (active) setNextPickLoading(false); });
    return () => { active = false; };
  }, [order, scanMode, nextPickRetry]);

  // Photos of every wine still to be picked this week, so tapping the
  // suggestion photo opens a carousel the courier can page through. Refreshes
  // on the same trigger as nextPick so picked SKUs drop out. Best-effort: a
  // failure just falls back to the single suggestion photo below.
  useEffect(() => {
    let active = true;
    api
      .weeklyPickPhotos(order.delivery_week ?? undefined)
      .then((res: WeeklyPickPhoto[]) => {
        if (active) setWeekPhotos(res.filter((p) => p.image_url));
      })
      .catch(() => { if (active) setWeekPhotos([]); });
    return () => { active = false; };
  }, [order, nextPickRetry]);

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

    const canvas = canvasRef.current;
    canvas.width = videoRef.current.videoWidth;
    canvas.height = videoRef.current.videoHeight;
    canvas.getContext("2d")!.drawImage(videoRef.current, 0, 0);

    // Freeze the captured frame over the live camera while we recognize it.
    setSnapshot(canvas.toDataURL("image/jpeg", 0.75));

    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.75),
    );
    if (!blob) {
      setScanning(false);
      setSnapshot(null);
      return;
    }

    // When the current order is already full, the suggestion points at another
    // active order — book against that one, else the completed order is rejected
    // (book_box requires an active context order). Matching still sweeps the
    // whole org/week, so the box lands on whichever line it matches.
    const bookOrderId =
      nextPick?.source === "other_order" ? nextPick.order_id : order.id;

    try {
      const confirmation: ConfirmationData = await api.bookBox(blob, bookOrderId, scanMode);
      onBooked(confirmation);
    } catch (err: unknown) {
      if (
        err instanceof ApiError &&
        err.status === 422 &&
        typeof err.detail === "object" &&
        err.detail !== null &&
        (err.detail as { error?: string }).error === "needs_reference_image"
      ) {
        setNeedsRef(err.detail as typeof needsRef);
        setSnapshot(null);
      } else {
        toast.error(err instanceof Error ? err.message : "Scanfout");
        setSnapshot(null);
      }
    } finally {
      setScanning(false);
    }
  }

  async function pickCandidate(skuId: number) {
    if (!needsRef) return;
    setRegistering(true);
    try {
      const confirmation: ConfirmationData = await api.registerReferenceAndBook(
        needsRef.register_token,
        skuId,
      );
      setNeedsRef(null);
      onBooked(confirmation);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Registratiefout");
    } finally {
      setRegistering(false);
    }
  }

  if (needsRef) {
    return (
      <>
        <Card className="p-3 mb-3">
          <p className="text-sm font-semibold">
            Alle open orders
          </p>
          <p className="text-xs text-muted-foreground">
            {openProgress
              ? `${formatBoxesBottles(openProgress.openBoxes, openProgress.openBottles)} open · ${openProgress.openOrders} orders`
              : "Open orders laden..."}
          </p>
        </Card>

        <Card className="p-3 mb-3 bg-amber-50 border-amber-200">
          <p className="text-sm font-semibold mb-1">Geen referentiefoto bekend</p>
          <p className="text-xs text-muted-foreground">
            Welke SKU staat er op de {SCAN_MODE_WORD[scanMode]}? Je scan wordt dan opgeslagen als
            referentiefoto en de boeking gaat door.
          </p>
        </Card>

        <img
          src={needsRef.scan_image_url}
          alt="scan"
          className="w-full rounded-lg mb-3 border"
        />

        <div className="space-y-2 mb-3">
          {needsRef.candidates.map((c) => (
            <button
              key={c.sku_id}
              onClick={() => pickCandidate(c.sku_id)}
              disabled={registering}
              className="w-full text-left p-3 rounded-lg border hover:bg-muted disabled:opacity-50"
            >
              <p className="font-semibold text-sm">{c.sku_name}</p>
              <p className="text-xs text-muted-foreground font-mono">
                {c.sku_code} &middot; nog {c.remaining_quantity} open in deze week
              </p>
            </button>
          ))}
        </div>

        <Button
          variant="outline"
          className="w-full"
          onClick={() => setNeedsRef(null)}
          disabled={registering}
        >
          Annuleer
        </Button>
      </>
    );
  }

  // Carousel of this week's open pick photos, starting on the current
  // suggestion. Only used when the suggested line is actually in the loaded
  // week list: next-pick can point at another active week (scheduled orders
  // are scoped across the whole org), while weeklyPickPhotos only loads this
  // order's week. When it isn't found we fall back to the single suggestion
  // photo so the lightbox never opens on the wrong wine.
  let carouselImages: string[];
  let carouselCaptions: string[];
  let carouselStart: number;
  const weekStart = weekPhotos.findIndex(
    (p) => p.order_line_id === nextPick?.order_line_id,
  );
  if (weekStart >= 0) {
    carouselImages = weekPhotos.map((p) => p.image_url as string);
    carouselCaptions = weekPhotos.map((p) => p.wine_name);
    carouselStart = weekStart;
  } else if (nextPick?.image_url) {
    carouselImages = [nextPick.image_url];
    carouselCaptions = [nextPick.sku_name];
    carouselStart = 0;
  } else {
    carouselImages = [];
    carouselCaptions = [];
    carouselStart = 0;
  }

  return (
    <>
      <Card className="p-3 mb-3">
        <p className="text-sm font-semibold">
          Alle open orders
        </p>
        <p className="text-xs text-muted-foreground">
          {openProgress
            ? `${formatBoxesBottles(openProgress.openBoxes, openProgress.openBottles)} open · ${openProgress.openOrders} orders`
            : "Open orders laden..."}
        </p>
      </Card>

      <ScanModeToggle mode={scanMode} onChange={onScanModeChange} />

      <p className="text-sm text-muted-foreground mb-3">
        Richt de camera op de {SCAN_MODE_WORD[scanMode]} en druk op Scan
      </p>
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
        {snapshot && (
          <>
            <img
              src={snapshot}
              alt="Gemaakte scan"
              className="absolute inset-0 w-full h-full object-cover"
            />
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/40">
              <div className="h-8 w-8 animate-spin rounded-full border-[3px] border-white/40 border-t-white" />
              <span className="text-white text-sm font-medium">Herkennen…</span>
            </div>
          </>
        )}
      </div>
      <Button
        size="lg"
        className="w-full text-lg h-14"
        onClick={capture}
        disabled={scanning || nextPickLoading || nextPick === null}
      >
        {scanning
          ? "Herkennen..."
          : nextPickLoading
            ? "Laden..."
            : nextPickFailed
              ? "Suggestie mislukt"
              : nextPick === null
                ? "Niets meer te scannen"
                : "Scan"}
      </Button>
      <button
        onClick={onBack}
        className="text-sm text-muted-foreground underline w-full text-center block mt-3"
      >
        Terug naar orders
      </button>

      {nextPickFailed && (
        <Card className="p-3 mt-4 bg-amber-50 border-amber-200">
          <p className="text-sm font-semibold mb-2">Suggestie laden mislukt</p>
          <p className="text-xs text-muted-foreground mb-3">
            Kon de volgende SKU niet ophalen. Probeer opnieuw om verder te scannen.
          </p>
          <Button
            variant="outline"
            className="w-full"
            onClick={() => setNextPickRetry((n) => n + 1)}
          >
            Opnieuw proberen
          </Button>
        </Card>
      )}

      {nextPick && (
        <Card className="p-3 mt-4">
          <p className="text-sm font-semibold mb-2">
            {nextPick.source === "this_order"
              ? "Volgende in deze order"
              : `Volgende — voor ${nextPick.customer_name ?? "andere klant"}`}
          </p>
          <div className="flex gap-3 items-center">
            <div className="w-20 h-20 shrink-0 rounded-lg overflow-hidden border bg-muted relative">
              {nextPick.image_url ? (
                <>
                  <img
                    src={nextPick.image_url}
                    alt={nextPick.sku_name}
                    className="w-full h-full object-cover cursor-zoom-in"
                    loading="lazy"
                    onClick={() => setNextPickLightbox(true)}
                  />
                  <div className="absolute bottom-1 right-1 bg-black/60 text-white p-0.5 rounded-full pointer-events-none">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="11" cy="11" r="7" />
                      <line x1="21" y1="21" x2="16.65" y2="16.65" />
                      <line x1="11" y1="8" x2="11" y2="14" />
                      <line x1="8" y1="11" x2="14" y2="11" />
                    </svg>
                  </div>
                </>
              ) : (
                <div className="flex h-full items-center justify-center px-1 text-center text-xs text-muted-foreground">
                  Geen foto
                </div>
              )}
            </div>
            <div className="min-w-0">
              <p className="text-sm font-medium truncate" title={nextPick.sku_name}>
                {nextPick.sku_name}
              </p>
              <p className="text-xs text-muted-foreground">
                nog {nextPick.remaining_quantity}
              </p>
            </div>
          </div>
        </Card>
      )}

      <ImageLightbox
        images={carouselImages}
        captions={carouselCaptions}
        startIndex={carouselStart}
        open={nextPickLightbox}
        onClose={() => setNextPickLightbox(false)}
      />
    </>
  );
}
