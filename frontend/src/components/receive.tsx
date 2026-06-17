import { useState, useEffect, useRef } from "react";
import { toast } from "@/App";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ImageSlideshow } from "@/components/image-slideshow";
import { ImageLightbox } from "@/components/image-lightbox";
import { QuantityPicker } from "@/components/quantity-picker";
import { Skeleton } from "@/components/ui/skeleton";
import { X } from "lucide-react";
import { formatBoxesBottles, formatBookedBoxesBottles } from "@/lib/units";

interface OrderLine {
  delivery_day: string;
  customer_name: string;
  quantity: number;
}

interface Order {
  id: number;
  reference: string;
  status: string;
  delivery_week?: string | null;
  organization_id?: number | null;
  customer_name: string | null;
  total_boxes: number;
  booked_boxes: number;
  total_bottles: number;
  booked_bottles: number;
  lines?: OrderLine[];
}

type ScanMode = "box" | "bottle";

const SCAN_MODE_WORD: Record<ScanMode, string> = { box: "doos", bottle: "fles" };
const SCAN_MODE_WORD_PLURAL: Record<ScanMode, string> = { box: "dozen", bottle: "flessen" };

/** Toggle between scanning boxes and loose bottles — selects the match pool. */
function ScanModeToggle({
  mode,
  onChange,
}: {
  mode: ScanMode;
  onChange: (mode: ScanMode) => void;
}) {
  return (
    <div className="flex rounded-md border border-border overflow-hidden mb-3 text-sm">
      <button
        type="button"
        onClick={() => onChange("box")}
        className={`flex-1 px-3 py-2 transition-colors ${
          mode === "box"
            ? "bg-primary text-primary-foreground"
            : "bg-background hover:bg-muted"
        }`}
      >
        Doos
      </button>
      <button
        type="button"
        onClick={() => onChange("bottle")}
        className={`flex-1 px-3 py-2 transition-colors border-l border-border ${
          mode === "bottle"
            ? "bg-primary text-primary-foreground"
            : "bg-background hover:bg-muted"
        }`}
      >
        Fles
      </button>
    </div>
  );
}

const DELIVERY_DAY_LABELS: Record<string, string> = {
  monday: "maandag",
  tuesday: "dinsdag",
  wednesday: "woensdag",
  thursday: "donderdag",
  friday: "vrijdag",
};

const DELIVERY_DAY_SHORT: Record<string, string> = {
  monday: "ma",
  tuesday: "di",
  wednesday: "wo",
  thursday: "do",
  friday: "vr",
};

interface BookingResult {
  id: number;
  order_id: number;
  order_line_id?: number;
  order_reference: string;
  context_order_id?: number | null;
  context_order_reference?: string | null;
  sku_id?: number;
  sku_code: string;
  sku_name: string;
  klant: string;
  rolcontainer: string;
  needs_confirmation?: boolean;
  scan_image_url?: string;
  reference_image_urls?: string[];
  confidence?: number;
  booked_quantity?: number;
  remaining_quantity?: number;
}

interface AlternativeMatch {
  sku_id: number;
  sku_code: string;
  sku_name: string;
  confidence: number;
  reference_image_url: string;
  reference_image_urls?: string[];
  confirmation_token: string;
}

interface DistributionLine {
  order_id: number;
  order_line_id: number;
  customer_name: string;
  rolcontainer: string;
  delivery_day: string;
  delivery_week?: string | null;
  ordered_quantity: number;
  booked_count: number;
  remaining_quantity: number;
  is_complete: boolean;
  is_context_order: boolean;
}

interface DistributionResult {
  sku_id: number;
  sku_code: string;
  sku_name: string;
  scope: string;
  total_remaining: number;
  lines: DistributionLine[];
}

interface ConfirmationData {
  needs_confirmation: true;
  confirmation_token: string;
  order_id?: number;
  order_line_id?: number;
  order_reference?: string;
  context_order_id?: number | null;
  context_order_reference?: string | null;
  sku_code: string;
  sku_name: string;
  confidence: number;
  klant?: string;
  rolcontainer?: string;
  scan_image_url: string;
  reference_image_url: string;
  reference_image_urls?: string[];
  alternatives?: AlternativeMatch[];
  remaining_quantity?: number;
  cap_for_customer?: number | null;
  ordered_by_customer?: number | null;
}

interface IdentifyResult {
  sku_id: number;
  sku_code: string;
  sku_name: string;
  confidence: number;
  needs_confirmation: boolean;
  confirmation_reason: string | null;
  alternatives?: AlternativeMatch[];
  scan_image_url?: string;
  reference_image_urls?: string[];
}

interface WeeklyPickPhoto {
  order_line_id: number;
  sku_id: number;
  wine_name: string;
  image_url: string | null;
  quantity: number;
  booked_count: number;
  customers: string[];
}

interface NextPick {
  sku_id: number;
  sku_name: string;
  order_line_id: number;
  image_url: string | null;
  remaining_quantity: number;
  source: "this_order" | "other_order";
  order_id: number;
  customer_name: string | null;
}

/* ---------- Week helpers (same logic as weekly-summary.tsx) ---------- */

function getISOWeek(date: Date): string {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7));
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${d.getUTCFullYear()}-W${String(weekNo).padStart(2, "0")}`;
}

function shiftWeek(week: string, delta: number): string {
  const [, y, w] = week.match(/^(\d{4})-W(\d{2})$/) || [];
  if (!y) return week;
  const monday = new Date(`${y}-01-04`);
  const dayOfWeek = monday.getDay() || 7;
  monday.setDate(monday.getDate() - dayOfWeek + 1 + (Number(w) - 1) * 7 + delta * 7);
  return getISOWeek(monday);
}

type Step = "select-order" | "this-week" | "scan" | "result" | "confirm" | "identify-scan" | "identify-result";

export function ReceivePage() {
  const [step, setStep] = useState<Step>("select-order");
  const [scanMode, setScanMode] = useState<ScanMode>("box");
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);
  const [overviewWeek, setOverviewWeek] = useState(() => getISOWeek(new Date()));
  const [lastBooking, setLastBooking] = useState<BookingResult | null>(null);
  const [lastIdentify, setLastIdentify] = useState<IdentifyResult | null>(null);
  const [pendingConfirmation, setPendingConfirmation] = useState<ConfirmationData | null>(null);

  function handleOrderSelected(order: Order) {
    setSelectedOrder(order);
    setStep("scan");
  }

  function handleThisWeek(week: string) {
    setOverviewWeek(week);
    setStep("this-week");
  }

  function handleBooked(booking: ConfirmationData) {
    setPendingConfirmation(booking);
    setStep("confirm");
  }

  function handleIdentified(result: IdentifyResult) {
    setLastIdentify(result);
    setStep("identify-result");
  }

  function handleConfirmed(booking: BookingResult) {
    setPendingConfirmation(null);
    setLastBooking(booking);
    setStep("result");
  }

  function scanNext() {
    setLastBooking(null);
    setPendingConfirmation(null);
    setStep("scan");
  }

  function reset() {
    setStep("select-order");
    setSelectedOrder(null);
    setLastBooking(null);
    setLastIdentify(null);
    setPendingConfirmation(null);
  }

  return (
    <div>
      <h2 className="text-xl font-bold mb-4">Scan & Boek</h2>

      {step === "select-order" && (
        <OrderSelectStep
          onSelect={handleOrderSelected}
          onIdentify={() => setStep("identify-scan")}
          onThisWeek={handleThisWeek}
        />
      )}

      {step === "this-week" && (
        <ThisWeekStep week={overviewWeek} onBack={reset} />
      )}

      {step === "scan" && selectedOrder && (
        <ScanStep
          order={selectedOrder}
          scanMode={scanMode}
          onScanModeChange={setScanMode}
          onBooked={handleBooked}
          onBack={reset}
        />
      )}

      {step === "confirm" && pendingConfirmation && selectedOrder && (
        <ConfirmStep
          confirmation={pendingConfirmation}
          scanMode={scanMode}
          onConfirmed={handleConfirmed}
          onReject={scanNext}
        />
      )}

      {step === "result" && lastBooking && selectedOrder && (
        <ResultStep
          booking={lastBooking}
          order={selectedOrder}
          scanMode={scanMode}
          onNext={scanNext}
          onDone={reset}
        />
      )}

      {step === "identify-scan" && (
        <IdentifyScanStep
          scanMode={scanMode}
          onScanModeChange={setScanMode}
          onIdentified={handleIdentified}
          onBack={reset}
        />
      )}

      {step === "identify-result" && (
        <IdentifyResultStep
          result={lastIdentify}
          onNext={() => { setLastIdentify(null); setStep("identify-scan"); }}
          onDone={reset}
        />
      )}
    </div>
  );
}

/* ---------- Order Card ---------- */

function OrderCard({ order: o, onSelect }: { order: Order; onSelect: (order: Order) => void }) {
  const days = [...new Set(o.lines?.map((l) => l.delivery_day) ?? [])];
  const dayOrderMap: Record<string, number> = {
    monday: 0,
    tuesday: 1,
    wednesday: 2,
    thursday: 3,
    friday: 4,
  };
  days.sort((a, b) => (dayOrderMap[a] ?? 9) - (dayOrderMap[b] ?? 9));

  return (
    <Card
      className="p-4 cursor-pointer active:scale-[0.98] transition-transform"
      onClick={() => onSelect(o)}
    >
      <div className="flex justify-between items-center mb-1">
        <span className="font-semibold">{o.reference}</span>
        <div className="flex gap-1">
          {days.map((d) => (
            <Badge key={d} variant="secondary" className="text-xs">
              {DELIVERY_DAY_SHORT[d] || d}
            </Badge>
          ))}
        </div>
      </div>
      <p className="text-sm text-muted-foreground">
        {o.customer_name ?? "—"}
      </p>
      <p className="text-sm text-muted-foreground">
        {formatBookedBoxesBottles(
          o.booked_boxes,
          o.total_boxes,
          o.booked_bottles,
          o.total_bottles,
        )}{" "}
        geboekt
      </p>
    </Card>
  );
}

/* ---------- Step 1: Select Active Order ---------- */

function OrderSelectStep({
  onSelect,
  onIdentify,
  onThisWeek,
}: {
  onSelect: (order: Order) => void;
  onIdentify: () => void;
  onThisWeek: (week: string) => void;
}) {
  const { user } = useAuth();
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [week, setWeek] = useState(() => getISOWeek(new Date()));

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const all = await api.listOrders(week);
        const active = all.filter((o: Order) => o.status === "active");
        // Sort by booked percentage ascending — least progress first
        active.sort((a: Order, b: Order) => {
          const totalA = a.total_boxes + a.total_bottles;
          const totalB = b.total_boxes + b.total_bottles;
          const pctA = totalA > 0 ? (a.booked_boxes + a.booked_bottles) / totalA : 0;
          const pctB = totalB > 0 ? (b.booked_boxes + b.booked_bottles) / totalB : 0;
          return pctA - pctB;
        });
        setOrders(active);
      } catch {
        toast.error("Kan orders niet laden");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [week]);

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
        Kies een actieve order om te scannen
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

      {(user?.role === "courier" || user?.is_platform_admin) && (
        <div className="grid grid-cols-2 gap-2 mt-4">
          <Button
            variant="secondary"
            className="w-full"
            onClick={() => onThisWeek(week)}
          >
            Deze week
          </Button>
          <Button
            variant="secondary"
            className="w-full"
            onClick={onIdentify}
          >
            Scan zonder order
          </Button>
        </div>
      )}
    </>
  );
}

/* ---------- This Week Overview ---------- */

function ThisWeekStep({ week: initialWeek, onBack }: { week: string; onBack: () => void }) {
  const [week, setWeek] = useState(initialWeek);
  const [items, setItems] = useState<WeeklyPickPhoto[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      try {
        const result = await api.weeklyPickPhotos(week);
        if (!cancelled) setItems(result);
      } catch (err: unknown) {
        if (!cancelled) {
          toast.error(err instanceof Error ? err.message : "Kan weekfoto's niet laden");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [week]);

  return (
    <>
      <div className="flex items-center justify-between gap-3 mb-4">
        <div>
          <h3 className="text-lg font-semibold">Deze week</h3>
          <p className="text-sm text-muted-foreground">{week}</p>
        </div>
        <Button variant="outline" size="sm" onClick={onBack}>
          Terug
        </Button>
      </div>

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

      {loading ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="aspect-square w-full rounded-lg" />
              <Skeleton className="h-4 w-4/5" />
            </div>
          ))}
        </div>
      ) : items.length === 0 ? (
        <p className="text-center text-muted-foreground py-10">
          Geen open pickfoto's voor deze week
        </p>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {items.map((item) => (
            <div key={item.sku_id} className="min-w-0">
              <div className="aspect-square overflow-hidden rounded-lg border border-border bg-muted">
                {item.image_url ? (
                  <img
                    src={item.image_url}
                    alt={item.wine_name}
                    className="h-full w-full object-cover"
                    loading="lazy"
                  />
                ) : (
                  <div className="flex h-full items-center justify-center px-3 text-center text-sm text-muted-foreground">
                    Geen foto
                  </div>
                )}
              </div>
              <p className="mt-2 truncate text-sm font-medium" title={item.wine_name}>
                {item.wine_name}
              </p>
              {item.customers.length > 0 && (
                <p
                  className="truncate text-xs text-muted-foreground"
                  title={item.customers.join(", ")}
                >
                  {item.customers.join(", ")}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </>
  );
}

/* ---------- Step 2: Camera Scan (with order) ---------- */

function ScanStep({
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
  const [nextPick, setNextPick] = useState<NextPick | null>(null);
  const [nextPickLoading, setNextPickLoading] = useState(true);
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
    api
      .nextPick(order.id)
      .then((res: NextPick | null) => { if (active) setNextPick(res); })
      .catch(() => { if (active) setNextPick(null); })
      .finally(() => { if (active) setNextPickLoading(false); });
    return () => { active = false; };
  }, [order]);

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

    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.75),
    );
    if (!blob) {
      setScanning(false);
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
      } else {
        toast.error(err instanceof Error ? err.message : "Scanfout");
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

      {nextPick && (
        <Card className="p-3 mt-4">
          <p className="text-sm font-semibold mb-2">
            {nextPick.source === "this_order"
              ? "Volgende in deze order"
              : `Order vol — suggestie voor ${nextPick.customer_name ?? "andere klant"}`}
          </p>
          <div className="flex gap-3 items-center">
            <div className="w-20 h-20 shrink-0 rounded-lg overflow-hidden border bg-muted">
              {nextPick.image_url ? (
                <img
                  src={nextPick.image_url}
                  alt={nextPick.sku_name}
                  className="w-full h-full object-cover"
                  loading="lazy"
                />
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
    </>
  );
}

/* ---------- Step 3: Result / Rolcontainer Assignment ---------- */

/* ---------- Verdeel-lijst: which customers this SKU still needs to go to ---------- */

function DistributionPanel({ orderId, skuId, refreshKey }: { orderId: number; skuId: number; refreshKey: number }) {
  const [data, setData] = useState<DistributionResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setFailed(false);
    // Each new box (refreshKey) or SKU is fresh info worth showing again, so a
    // manual dismiss only hides the current list — not every future one.
    setDismissed(false);
    api
      .getDistribution(orderId, skuId)
      .then((res: DistributionResult) => { if (active) setData(res); })
      .catch(() => { if (active) setFailed(true); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
    // refreshKey bumps after each extra booking so the verdeel-lijst re-fetches its
    // gescand/nog counts instead of going stale against the box we just booked.
  }, [orderId, skuId, refreshKey]);

  if (dismissed) return null;

  if (loading) {
    return (
      <Card className="p-4 mb-4">
        <Skeleton className="h-5 w-44 mb-3" />
        <Skeleton className="h-4 w-full mb-2" />
        <Skeleton className="h-4 w-3/4" />
      </Card>
    );
  }

  // Hide silently on failure, or when this SKU only goes to the one customer we
  // just booked — there is nothing extra to distribute. Read-only: never blocks.
  if (failed || !data || data.lines.length <= 1) return null;

  return (
    <Card className="p-4 mb-4">
      <div className="flex items-center justify-between gap-2 mb-3">
        <p className="text-sm font-semibold">Deze SKU ook nog naar</p>
        <div className="flex items-center gap-1 shrink-0">
          <Badge variant="secondary" className="shrink-0">
            nog {data.total_remaining} te verdelen
          </Badge>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7 -mr-1 text-muted-foreground"
            aria-label="Verdeel-lijst sluiten"
            onClick={() => setDismissed(true)}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>
      <ul className="space-y-2">
        {data.lines.map((line) => (
          <li
            key={line.order_line_id}
            className={`flex items-center justify-between gap-2 text-sm ${line.is_context_order ? "opacity-50" : ""}`}
          >
            <div className="min-w-0">
              <p className="font-medium truncate">
                {line.customer_name}
                {line.is_context_order && (
                  <span className="text-muted-foreground font-normal"> (deze doos)</span>
                )}
              </p>
              <p className="text-xs text-muted-foreground">
                {DELIVERY_DAY_LABELS[line.delivery_day] ?? line.delivery_day}
                {" · besteld "}{line.ordered_quantity}
                {" · gescand "}{line.booked_count}
              </p>
            </div>
            {line.is_complete ? (
              <Badge variant="secondary" className="shrink-0">✓ klaar</Badge>
            ) : (
              <Badge className="shrink-0">nog {line.remaining_quantity}</Badge>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function ResultStep({
  booking,
  order,
  scanMode,
  onNext,
  onDone,
}: {
  booking: BookingResult;
  order: Order;
  scanMode: ScanMode;
  onNext: () => void;
  onDone: () => void;
}) {
  const referenceImages = booking.reference_image_urls ?? [];
  const [remaining, setRemaining] = useState(booking.remaining_quantity ?? 0);
  const [moreQuantity, setMoreQuantity] = useState(1);
  const [bookingMore, setBookingMore] = useState(false);
  const [totalBooked, setTotalBooked] = useState(booking.booked_quantity ?? 1);
  const [lightbox, setLightbox] = useState<{ images: string[]; index: number } | null>(null);

  async function handleBookMore() {
    if (!booking.order_line_id) return;
    setBookingMore(true);
    try {
      const result: BookingResult = await api.bookMore(
        booking.order_line_id,
        moreQuantity,
        booking.scan_image_url ?? "",
      );
      const actualBooked = result.booked_quantity ?? moreQuantity;
      setTotalBooked((prev) => prev + actualBooked);
      setRemaining(result.remaining_quantity ?? 0);
      setMoreQuantity(1);
      toast.success(`${actualBooked}× extra geboekt`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Boeken mislukt";
      if (msg.includes("allocation_cap_reached") || msg.includes("Toewijzingslimiet")) {
        toast.error("Toewijzingslimiet bereikt — niet meer beschikbaar voor deze klant vandaag");
      } else {
        toast.error(msg);
      }
    } finally {
      setBookingMore(false);
    }
  }

  return (
    <>
      <div className="p-6 rounded-lg bg-green-600/20 border-2 border-green-600 text-center mb-4">
        <p className="text-green-400 text-2xl font-bold mb-2">
          Zet op rolcontainer
        </p>
        <p className="text-green-300 text-3xl font-black">
          {booking.rolcontainer}
        </p>
        {totalBooked > 1 && (
          <p className="text-green-400 text-lg mt-1">
            {totalBooked}× geboekt
          </p>
        )}
      </div>

      <Card className="p-4 mb-4">
        <div className="space-y-1 mb-3">
          <p className="text-sm">
            <span className="text-muted-foreground">Product:</span>{" "}
            <span className="font-semibold">{booking.sku_name}</span>
          </p>
          <p className="text-sm">
            <span className="text-muted-foreground">SKU:</span>{" "}
            <span className="font-mono">{booking.sku_code}</span>
          </p>
          {booking.confidence != null && booking.confidence > 0 && (
            <p className="text-sm">
              <span className="text-muted-foreground">Zekerheid:</span>{" "}
              {Math.round(booking.confidence * 100)}%
            </p>
          )}
          <p className="text-sm">
            <span className="text-muted-foreground">Order:</span>{" "}
            {booking.order_reference}
          </p>
          {booking.context_order_reference && booking.context_order_reference !== booking.order_reference && (
            <p className="text-sm">
              <span className="text-muted-foreground">Gestart vanuit:</span>{" "}
              {booking.context_order_reference}
            </p>
          )}
          <p className="text-sm">
            <span className="text-muted-foreground">Klant:</span>{" "}
            {booking.klant}
          </p>
        </div>

        {/* Scan vs referentie vergelijking */}
        {(booking.scan_image_url || referenceImages.length > 0) && (
          <div className="grid grid-cols-2 gap-3">
            {booking.scan_image_url && (
              <div>
                <p className="text-xs text-muted-foreground mb-1 font-semibold text-center">Scan</p>
                <div className="aspect-square rounded-lg overflow-hidden bg-black relative">
                  <img
                    src={booking.scan_image_url}
                    alt="Scan"
                    className="w-full h-full object-cover cursor-zoom-in"
                    onClick={() => setLightbox({ images: [booking.scan_image_url!], index: 0 })}
                  />
                  <div className="absolute bottom-2 right-2 bg-black/60 text-white p-1 rounded-full pointer-events-none">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="11" cy="11" r="7" />
                      <line x1="21" y1="21" x2="16.65" y2="16.65" />
                      <line x1="11" y1="8" x2="11" y2="14" />
                      <line x1="8" y1="11" x2="14" y2="11" />
                    </svg>
                  </div>
                </div>
              </div>
            )}
            {referenceImages.length > 0 && (
              <div>
                <p className="text-xs text-muted-foreground mb-1 font-semibold text-center">Referentie</p>
                <ImageSlideshow
                  images={referenceImages}
                  maxWidth="100%"
                  onImageClick={(i) => setLightbox({ images: referenceImages, index: i })}
                />
              </div>
            )}
          </div>
        )}
      </Card>

      {/* Book more identical boxes */}
      {remaining > 0 && booking.order_line_id && (
        <Card className="p-4 mb-4 border-2 border-blue-600/30">
          <p className="text-sm font-semibold text-center mb-3">
            Nog {remaining} dezelfde in deze order
          </p>
          <QuantityPicker
            value={moreQuantity}
            onChange={setMoreQuantity}
            max={remaining}
          />
          <Button
            size="lg"
            className="w-full h-12 text-base mt-3"
            onClick={handleBookMore}
            disabled={bookingMore}
          >
            {bookingMore ? "Boeken..." : `${moreQuantity}× extra boeken`}
          </Button>
        </Card>
      )}

      {/* Read-only verdeel-lijst: which other customers this SKU still needs to go to */}
      {booking.sku_id != null && (
        <DistributionPanel orderId={order.id} skuId={booking.sku_id} refreshKey={totalBooked} />
      )}

      <div className="flex flex-col gap-3">
        <Button size="lg" className="w-full h-14 text-lg" onClick={onNext}>
          Volgende {SCAN_MODE_WORD[scanMode]} scannen
        </Button>
        <Button variant="secondary" className="w-full" onClick={onDone}>
          Terug naar orders
        </Button>
      </div>

      <ImageLightbox
        images={lightbox?.images ?? []}
        startIndex={lightbox?.index ?? 0}
        open={lightbox !== null}
        onClose={() => setLightbox(null)}
      />
    </>
  );
}

/* ---------- Step 2b: Human Confirmation (low-quality match) ---------- */

function ConfirmStep({
  confirmation,
  scanMode,
  onConfirmed,
  onReject,
}: {
  confirmation: ConfirmationData;
  scanMode: ScanMode;
  onConfirmed: (booking: BookingResult) => void;
  onReject: () => void;
}) {
  const unitWord = SCAN_MODE_WORD[scanMode];
  const unitWordPlural = SCAN_MODE_WORD_PLURAL[scanMode];
  const [confirming, setConfirming] = useState(false);
  const [quantity, setQuantity] = useState(1);
  const [lightbox, setLightbox] = useState<{ images: string[]; index: number } | null>(null);
  const hasAlternatives = confirmation.alternatives && confirmation.alternatives.length > 0;
  const capRemaining = confirmation.cap_for_customer != null
    ? confirmation.cap_for_customer
    : (confirmation.remaining_quantity ?? 1);
  const maxQuantity = Math.min(confirmation.remaining_quantity ?? 1, capRemaining);
  const hasCap = confirmation.cap_for_customer != null
    && confirmation.ordered_by_customer != null
    && confirmation.cap_for_customer < confirmation.ordered_by_customer;
  const highConfidence = !hasAlternatives && confirmation.confidence >= 0.84;

  async function handleConfirm(token?: string) {
    setConfirming(true);
    try {
      const booking: BookingResult = await api.confirmBooking(
        token ?? confirmation.confirmation_token,
        quantity,
      );
      onConfirmed(booking);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Bevestiging mislukt");
    } finally {
      setConfirming(false);
    }
  }

  return (
    <>
      {hasAlternatives ? (
        <div className="p-4 rounded-lg bg-orange-600/20 border-2 border-orange-600 text-center mb-4">
          <p className="text-orange-400 text-xl font-bold mb-1">
            Meerdere matches
          </p>
          <p className="text-orange-300 text-sm">
            Vergelijkbare producten gevonden — welke {unitWord} is dit?
          </p>
        </div>
      ) : highConfidence ? (
        <div className="p-4 rounded-lg bg-green-600/20 border-2 border-green-600 text-center mb-4">
          <p className="text-green-400 text-xl font-bold mb-1">
            Match gevonden
          </p>
          <p className="text-green-300 text-sm">
            {Math.round(confirmation.confidence * 100)}% zekerheid — bevestig om te boeken
          </p>
        </div>
      ) : (
        <div className="p-4 rounded-lg bg-yellow-600/20 border-2 border-yellow-600 text-center mb-4">
          <p className="text-yellow-400 text-xl font-bold mb-1">
            Controleer match
          </p>
          <p className="text-yellow-300 text-sm">
            Onzekere match — bevestig handmatig
          </p>
        </div>
      )}

      {/* Rolcontainer assignment */}
      {confirmation.rolcontainer && (
        <div className="p-4 rounded-lg bg-muted/50 border text-center mb-4">
          <p className="text-sm text-muted-foreground mb-1">Zet op rolcontainer</p>
          <p className="text-xl font-black">{confirmation.rolcontainer}</p>
        </div>
      )}

      {/* Scan image */}
      <Card className="p-4 mb-4">
        <p className="text-xs text-muted-foreground mb-2 font-semibold">Uw scan</p>
        <div className="aspect-square rounded-lg overflow-hidden bg-black max-w-[200px] mx-auto relative">
          <img
            src={confirmation.scan_image_url}
            alt="Scan"
            className="w-full h-full object-cover cursor-zoom-in"
            onClick={() => setLightbox({ images: [confirmation.scan_image_url], index: 0 })}
          />
          <div className="absolute bottom-2 right-2 bg-black/60 text-white p-1 rounded-full pointer-events-none">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="11" cy="11" r="7" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
              <line x1="11" y1="8" x2="11" y2="14" />
              <line x1="8" y1="11" x2="14" y2="11" />
            </svg>
          </div>
        </div>
      </Card>

      {/* Quantity picker */}
      {maxQuantity > 1 && (
        <Card className="p-4 mb-4">
          <p className="text-xs text-muted-foreground mb-2 font-semibold text-center">
            Hoeveel {unitWordPlural} van dit product?
          </p>
          <QuantityPicker value={quantity} onChange={setQuantity} max={maxQuantity} />
          <p className="text-xs text-muted-foreground mt-2 text-center">
            {maxQuantity} over in deze order
          </p>
          {hasCap && (
            <p className="text-xs text-orange-400 mt-2 text-center">
              Max voor {confirmation.klant} vandaag: {confirmation.cap_for_customer} van {confirmation.ordered_by_customer} {unitWordPlural} {confirmation.sku_name}
            </p>
          )}
        </Card>
      )}
      {maxQuantity <= 1 && hasCap && (
        <Card className="p-4 mb-4">
          <p className="text-xs text-orange-400 text-center">
            Max voor {confirmation.klant} vandaag: {confirmation.cap_for_customer} van {confirmation.ordered_by_customer} {unitWordPlural} {confirmation.sku_name}
          </p>
        </Card>
      )}

      {hasAlternatives ? (
        <>
          {/* Best match */}
          <Card className="p-4 mb-3 border-2 border-green-600/50">
            <div className="space-y-1 mb-3">
              <p className="text-sm font-semibold text-green-400">Beste match</p>
              <p className="text-sm">
                <span className="text-muted-foreground">Product:</span>{" "}
                <span className="font-semibold">{confirmation.sku_name}</span>
              </p>
              <p className="text-sm">
                <span className="text-muted-foreground">SKU:</span>{" "}
                <span className="font-mono">{confirmation.sku_code}</span>
              </p>
              <p className="text-sm">
                <span className="text-muted-foreground">Zekerheid:</span>{" "}
                {Math.round(confirmation.confidence * 100)}%
              </p>
            </div>
            <ImageSlideshow
              images={confirmation.reference_image_urls?.length ? confirmation.reference_image_urls : (confirmation.reference_image_url ? [confirmation.reference_image_url] : [])}
              maxWidth="160px"
              onImageClick={(i) => {
                const imgs = confirmation.reference_image_urls?.length ? confirmation.reference_image_urls : (confirmation.reference_image_url ? [confirmation.reference_image_url] : []);
                setLightbox({ images: imgs, index: i });
              }}
            />
            <Button
              size="lg"
              className="w-full h-12 text-base bg-green-600 hover:bg-green-700 mt-3"
              onClick={() => handleConfirm()}
              disabled={confirming}
            >
              {confirming ? "Boeken..." : `Dit is ${confirmation.sku_name}${quantity > 1 ? ` (${quantity}×)` : ""}`}
            </Button>
          </Card>

          {/* Alternatives */}
          {confirmation.alternatives!.map((alt) => (
            <Card key={alt.sku_id} className="p-4 mb-3 border border-muted">
              <div className="space-y-1 mb-3">
                <p className="text-sm">
                  <span className="text-muted-foreground">Product:</span>{" "}
                  <span className="font-semibold">{alt.sku_name}</span>
                </p>
                <p className="text-sm">
                  <span className="text-muted-foreground">SKU:</span>{" "}
                  <span className="font-mono">{alt.sku_code}</span>
                </p>
                <p className="text-sm">
                  <span className="text-muted-foreground">Zekerheid:</span>{" "}
                  {Math.round(alt.confidence * 100)}%
                </p>
              </div>
              <ImageSlideshow
                images={alt.reference_image_urls?.length ? alt.reference_image_urls : (alt.reference_image_url ? [alt.reference_image_url] : [])}
                maxWidth="160px"
                onImageClick={(i) => {
                  const imgs = alt.reference_image_urls?.length ? alt.reference_image_urls : (alt.reference_image_url ? [alt.reference_image_url] : []);
                  setLightbox({ images: imgs, index: i });
                }}
              />
              <Button
                size="lg"
                className="w-full h-12 text-base mt-3"
                variant="outline"
                onClick={() => handleConfirm(alt.confirmation_token)}
                disabled={confirming}
              >
                {confirming ? "Boeken..." : `Dit is ${alt.sku_name}`}
              </Button>
            </Card>
          ))}

          <Button
            variant="destructive"
            size="lg"
            className="w-full h-14 text-lg mt-2"
            onClick={onReject}
            disabled={confirming}
          >
            Geen van deze — opnieuw scannen
          </Button>
        </>
      ) : (
        <>
          <Card className="p-4 mb-4">
            <div className="space-y-1 mb-3">
              <p className="text-sm">
                <span className="text-muted-foreground">Product:</span>{" "}
                <span className="font-semibold">{confirmation.sku_name}</span>
              </p>
              <p className="text-sm">
                <span className="text-muted-foreground">SKU:</span>{" "}
                <span className="font-mono">{confirmation.sku_code}</span>
              </p>
              <p className="text-sm">
                <span className="text-muted-foreground">Zekerheid:</span>{" "}
                {Math.round(confirmation.confidence * 100)}%
              </p>
              {confirmation.klant && (
                <p className="text-sm">
                  <span className="text-muted-foreground">Klant:</span>{" "}
                  {confirmation.klant}
                </p>
              )}
            </div>

            <p className="text-xs text-muted-foreground mb-2 font-semibold">
              Is dit dezelfde {unitWord}?
            </p>
            <ImageSlideshow
              images={confirmation.reference_image_urls?.length ? confirmation.reference_image_urls : (confirmation.reference_image_url ? [confirmation.reference_image_url] : [])}
              maxWidth="200px"
              onImageClick={(i) => {
                const imgs = confirmation.reference_image_urls?.length ? confirmation.reference_image_urls : (confirmation.reference_image_url ? [confirmation.reference_image_url] : []);
                setLightbox({ images: imgs, index: i });
              }}
            />
          </Card>

          <div className="flex flex-col gap-3">
            <Button
              size="lg"
              className="w-full h-14 text-lg bg-green-600 hover:bg-green-700"
              onClick={() => handleConfirm()}
              disabled={confirming}
            >
              {confirming ? "Boeken..." : `Ja, dit klopt${quantity > 1 ? ` (${quantity}×)` : ""}`}
            </Button>
            <Button
              variant="destructive"
              size="lg"
              className="w-full h-14 text-lg"
              onClick={onReject}
              disabled={confirming}
            >
              Nee, opnieuw scannen
            </Button>
          </div>
        </>
      )}

      <ImageLightbox
        images={lightbox?.images ?? []}
        startIndex={lightbox?.index ?? 0}
        open={lightbox !== null}
        onClose={() => setLightbox(null)}
      />
    </>
  );
}

/* ---------- Identify Scan (without order) ---------- */

function IdentifyScanStep({
  scanMode,
  onScanModeChange,
  onIdentified,
  onBack,
}: {
  scanMode: ScanMode;
  onScanModeChange: (mode: ScanMode) => void;
  onIdentified: (result: IdentifyResult) => void;
  onBack: () => void;
}) {
  const [scanning, setScanning] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);

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

    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.75),
    );
    if (!blob) {
      setScanning(false);
      return;
    }

    try {
      const result: IdentifyResult | null = await api.identifyBox(blob, scanMode);
      if (result) {
        onIdentified(result);
      } else {
        toast.error(
          `${scanMode === "bottle" ? "Fles" : "Doos"} niet herkend — geen match gevonden`,
        );
      }
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Scanfout");
    } finally {
      setScanning(false);
    }
  }

  return (
    <>
      <Card className="p-3 mb-3">
        <p className="text-sm font-semibold">Scan zonder order</p>
        <p className="text-xs text-muted-foreground">
          Identificeer een {SCAN_MODE_WORD[scanMode]} zonder te boeken
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
      </div>
      <Button
        size="lg"
        className="w-full text-lg h-14"
        onClick={capture}
        disabled={scanning}
      >
        {scanning ? "Herkennen..." : "Scan"}
      </Button>
      <button
        onClick={onBack}
        className="text-sm text-muted-foreground underline w-full text-center block mt-3"
      >
        Terug naar orders
      </button>
    </>
  );
}

/* ---------- Identify Result (without order) ---------- */

function IdentifyResultStep({
  result,
  onNext,
  onDone,
}: {
  result: IdentifyResult | null;
  onNext: () => void;
  onDone: () => void;
}) {
  const [lightbox, setLightbox] = useState<{ images: string[]; index: number } | null>(null);

  if (!result) return null;

  const referenceImages = result.reference_image_urls ?? [];

  return (
    <>
      {result.needs_confirmation ? (
        <div className="p-6 rounded-lg bg-yellow-600/20 border-2 border-yellow-600 text-center mb-4">
          <p className="text-yellow-400 text-2xl font-bold mb-2">
            Controleer resultaat
          </p>
          <p className="text-yellow-300 text-xl font-black">
            {result.sku_name}
          </p>
          <p className="text-yellow-400/80 text-sm mt-2">
            Lage betrouwbaarheid — controleer of dit klopt
          </p>
        </div>
      ) : (
        <div className="p-6 rounded-lg bg-blue-600/20 border-2 border-blue-600 text-center mb-4">
          <p className="text-blue-400 text-2xl font-bold mb-2">
            Product herkend
          </p>
          <p className="text-blue-300 text-xl font-black">
            {result.sku_name}
          </p>
        </div>
      )}

      <Card className="p-4 mb-4">
        <div className="space-y-1 mb-3">
          <p className="text-sm">
            <span className="text-muted-foreground">Product:</span>{" "}
            <span className="font-semibold">{result.sku_name}</span>
          </p>
          <p className="text-sm">
            <span className="text-muted-foreground">SKU:</span>{" "}
            <span className="font-mono">{result.sku_code}</span>
          </p>
          <p className="text-sm">
            <span className="text-muted-foreground">Zekerheid:</span>{" "}
            {Math.round(result.confidence * 100)}%
          </p>
        </div>

        {/* Scan vs referentie vergelijking */}
        {(result.scan_image_url || (result.reference_image_urls && result.reference_image_urls.length > 0)) && (
          <div className="grid grid-cols-2 gap-3">
            {result.scan_image_url && (
              <div>
                <p className="text-xs text-muted-foreground mb-1 font-semibold text-center">Scan</p>
                <div className="aspect-square rounded-lg overflow-hidden bg-black relative">
                  <img
                    src={result.scan_image_url}
                    alt="Scan"
                    className="w-full h-full object-cover cursor-zoom-in"
                    onClick={() => setLightbox({ images: [result.scan_image_url!], index: 0 })}
                  />
                  <div className="absolute bottom-2 right-2 bg-black/60 text-white p-1 rounded-full pointer-events-none">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="11" cy="11" r="7" />
                      <line x1="21" y1="21" x2="16.65" y2="16.65" />
                      <line x1="11" y1="8" x2="11" y2="14" />
                      <line x1="8" y1="11" x2="14" y2="11" />
                    </svg>
                  </div>
                </div>
              </div>
            )}
            {referenceImages.length > 0 && (
              <div>
                <p className="text-xs text-muted-foreground mb-1 font-semibold text-center">Referentie</p>
                <ImageSlideshow
                  images={referenceImages}
                  maxWidth="100%"
                  onImageClick={(i) => setLightbox({ images: referenceImages, index: i })}
                />
              </div>
            )}
          </div>
        )}
      </Card>

      <div className="flex flex-col gap-3">
        <Button size="lg" className="w-full h-14 text-lg" onClick={onNext}>
          Opnieuw scannen
        </Button>
        <Button variant="secondary" className="w-full" onClick={onDone}>
          Terug naar orders
        </Button>
      </div>

      <ImageLightbox
        images={lightbox?.images ?? []}
        startIndex={lightbox?.index ?? 0}
        open={lightbox !== null}
        onClose={() => setLightbox(null)}
      />
    </>
  );
}
