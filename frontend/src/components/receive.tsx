import { useState, useEffect, useRef } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ImageSlideshow } from "@/components/image-slideshow";
import { QuantityPicker } from "@/components/quantity-picker";

interface Order {
  id: number;
  reference: string;
  status: string;
  merchant_name: string;
  total_boxes: number;
  booked_boxes: number;
}

interface BookingResult {
  id: number;
  order_id: number;
  order_reference: string;
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

interface ConfirmationData {
  needs_confirmation: true;
  confirmation_token: string;
  sku_code: string;
  sku_name: string;
  confidence: number;
  scan_image_url: string;
  reference_image_url: string;
  reference_image_urls?: string[];
  alternatives?: AlternativeMatch[];
  remaining_quantity?: number;
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

type Step = "select-order" | "scan" | "result" | "confirm" | "identify-scan" | "identify-result";

export function ReceivePage() {
  const [step, setStep] = useState<Step>("select-order");
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);
  const [lastBooking, setLastBooking] = useState<BookingResult | null>(null);
  const [lastIdentify, setLastIdentify] = useState<IdentifyResult | null>(null);
  const [pendingConfirmation, setPendingConfirmation] = useState<ConfirmationData | null>(null);

  function handleOrderSelected(order: Order) {
    setSelectedOrder(order);
    setStep("scan");
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
        />
      )}

      {step === "scan" && selectedOrder && (
        <ScanStep
          order={selectedOrder}
          onBooked={handleBooked}
          onBack={reset}
        />
      )}

      {step === "confirm" && pendingConfirmation && selectedOrder && (
        <ConfirmStep
          confirmation={pendingConfirmation}
          onConfirmed={handleConfirmed}
          onReject={scanNext}
        />
      )}

      {step === "result" && lastBooking && selectedOrder && (
        <ResultStep
          booking={lastBooking}
          order={selectedOrder}
          onNext={scanNext}
          onDone={reset}
        />
      )}

      {step === "identify-scan" && (
        <IdentifyScanStep
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

/* ---------- Step 1: Select Active Order ---------- */

function OrderSelectStep({
  onSelect,
  onIdentify,
}: {
  onSelect: (order: Order) => void;
  onIdentify: () => void;
}) {
  const { user } = useAuth();
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const all = await api.listOrders();
        setOrders(all.filter((o: Order) => o.status === "active"));
      } catch {
        toast.error("Kan orders niet laden");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) {
    return <p className="text-center text-muted-foreground py-10">Laden...</p>;
  }

  return (
    <>
      <p className="text-sm text-muted-foreground mb-3">
        Kies een actieve order om dozen te scannen
      </p>
      {orders.length === 0 ? (
        <p className="text-center text-muted-foreground py-10">
          Geen actieve orders
        </p>
      ) : (
        <div className="space-y-3">
          {orders.map((o) => (
            <Card
              key={o.id}
              className="p-4 cursor-pointer active:scale-[0.98] transition-transform"
              onClick={() => onSelect(o)}
            >
              <div className="flex justify-between items-center mb-1">
                <span className="font-semibold">{o.reference}</span>
                <Badge variant="active">Actief</Badge>
              </div>
              <p className="text-sm text-muted-foreground">
                {o.merchant_name}
              </p>
              <p className="text-sm text-muted-foreground">
                {o.booked_boxes}/{o.total_boxes} dozen geboekt
              </p>
            </Card>
          ))}
        </div>
      )}

      {user?.role === "admin" && (
        <Button
          variant="secondary"
          className="w-full mt-4"
          onClick={onIdentify}
        >
          Scan zonder order
        </Button>
      )}
    </>
  );
}

/* ---------- Step 2: Camera Scan (with order) ---------- */

function ScanStep({
  order,
  onBooked,
  onBack,
}: {
  order: Order;
  onBooked: (booking: ConfirmationData) => void;
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
      const confirmation: ConfirmationData = await api.bookBox(blob, order.id);
      onBooked(confirmation);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Scanfout");
    } finally {
      setScanning(false);
    }
  }

  return (
    <>
      <Card className="p-3 mb-3">
        <p className="text-sm font-semibold">{order.reference}</p>
        <p className="text-xs text-muted-foreground">
          {order.merchant_name} &middot; {order.booked_boxes}/{order.total_boxes} dozen
        </p>
      </Card>

      <p className="text-sm text-muted-foreground mb-3">
        Richt de camera op de doos en druk op Scan
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

/* ---------- Step 3: Result / Rolcontainer Assignment ---------- */

function ResultStep({
  booking,
  order,
  onNext,
  onDone,
}: {
  booking: BookingResult;
  order: Order;
  onNext: () => void;
  onDone: () => void;
}) {
  const referenceImages = booking.reference_image_urls ?? [];
  const [remaining, setRemaining] = useState(booking.remaining_quantity ?? 0);
  const [moreQuantity, setMoreQuantity] = useState(1);
  const [bookingMore, setBookingMore] = useState(false);
  const [totalBooked, setTotalBooked] = useState(booking.booked_quantity ?? 1);

  async function handleBookMore() {
    if (!booking.sku_id || !booking.order_id) return;
    setBookingMore(true);
    try {
      const result: BookingResult = await api.bookMore(
        booking.order_id,
        booking.sku_id,
        moreQuantity,
        booking.scan_image_url ?? "",
      );
      const actualBooked = result.booked_quantity ?? moreQuantity;
      setTotalBooked((prev) => prev + actualBooked);
      setRemaining(result.remaining_quantity ?? 0);
      setMoreQuantity(1);
      toast.success(`${actualBooked}× extra geboekt`);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Boeken mislukt");
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
                <div className="aspect-square rounded-lg overflow-hidden bg-black">
                  <img
                    src={booking.scan_image_url}
                    alt="Scan"
                    className="w-full h-full object-cover"
                  />
                </div>
              </div>
            )}
            {referenceImages.length > 0 && (
              <div>
                <p className="text-xs text-muted-foreground mb-1 font-semibold text-center">Referentie</p>
                <ImageSlideshow images={referenceImages} maxWidth="100%" />
              </div>
            )}
          </div>
        )}
      </Card>

      {/* Book more identical boxes */}
      {remaining > 0 && booking.sku_id && (
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

      <div className="flex flex-col gap-3">
        <Button size="lg" className="w-full h-14 text-lg" onClick={onNext}>
          Volgende doos scannen
        </Button>
        <Button variant="secondary" className="w-full" onClick={onDone}>
          Terug naar orders
        </Button>
      </div>
    </>
  );
}

/* ---------- Step 2b: Human Confirmation (low-quality match) ---------- */

function ConfirmStep({
  confirmation,
  onConfirmed,
  onReject,
}: {
  confirmation: ConfirmationData;
  onConfirmed: (booking: BookingResult) => void;
  onReject: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [quantity, setQuantity] = useState(1);
  const hasAlternatives = confirmation.alternatives && confirmation.alternatives.length > 0;
  const maxQuantity = confirmation.remaining_quantity ?? 1;
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
            Vergelijkbare producten gevonden — welke doos is dit?
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

      {/* Scan image */}
      <Card className="p-4 mb-4">
        <p className="text-xs text-muted-foreground mb-2 font-semibold">Uw scan</p>
        <div className="aspect-square rounded-lg overflow-hidden bg-black max-w-[200px] mx-auto">
          <img
            src={confirmation.scan_image_url}
            alt="Scan"
            className="w-full h-full object-cover"
          />
        </div>
      </Card>

      {/* Quantity picker */}
      {maxQuantity > 1 && (
        <Card className="p-4 mb-4">
          <p className="text-xs text-muted-foreground mb-2 font-semibold text-center">
            Hoeveel dozen van dit product?
          </p>
          <QuantityPicker value={quantity} onChange={setQuantity} max={maxQuantity} />
          <p className="text-xs text-muted-foreground mt-2 text-center">
            {maxQuantity} over in deze order
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
            </div>

            <p className="text-xs text-muted-foreground mb-2 font-semibold">
              Is dit dezelfde doos?
            </p>
            <ImageSlideshow
              images={confirmation.reference_image_urls?.length ? confirmation.reference_image_urls : (confirmation.reference_image_url ? [confirmation.reference_image_url] : [])}
              maxWidth="200px"
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
    </>
  );
}

/* ---------- Identify Scan (without order) ---------- */

function IdentifyScanStep({
  onIdentified,
  onBack,
}: {
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
      const result: IdentifyResult | null = await api.identifyBox(blob);
      if (result) {
        onIdentified(result);
      } else {
        toast.error("Doos niet herkend — geen match gevonden");
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
          Identificeer een doos zonder te boeken
        </p>
      </Card>

      <p className="text-sm text-muted-foreground mb-3">
        Richt de camera op de doos en druk op Scan
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
  if (!result) return null;

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
                <div className="aspect-square rounded-lg overflow-hidden bg-black">
                  <img
                    src={result.scan_image_url}
                    alt="Scan"
                    className="w-full h-full object-cover"
                  />
                </div>
              </div>
            )}
            {result.reference_image_urls && result.reference_image_urls.length > 0 && (
              <div>
                <p className="text-xs text-muted-foreground mb-1 font-semibold text-center">Referentie</p>
                <ImageSlideshow images={result.reference_image_urls} maxWidth="100%" />
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
    </>
  );
}
