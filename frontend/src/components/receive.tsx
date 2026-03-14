import { useState, useEffect, useRef, useCallback } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

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
  sku_code: string;
  sku_name: string;
  klant: string;
  rolcontainer: string;
}

type Step = "select-order" | "scan" | "result";

export function ReceivePage() {
  const [step, setStep] = useState<Step>("select-order");
  const [selectedOrder, setSelectedOrder] = useState<Order | null>(null);
  const [lastBooking, setLastBooking] = useState<BookingResult | null>(null);

  function handleOrderSelected(order: Order) {
    setSelectedOrder(order);
    setStep("scan");
  }

  function handleBooked(booking: BookingResult) {
    setLastBooking(booking);
    setStep("result");
  }

  function scanNext() {
    setLastBooking(null);
    setStep("scan");
  }

  function reset() {
    setStep("select-order");
    setSelectedOrder(null);
    setLastBooking(null);
  }

  return (
    <div>
      <h2 className="text-xl font-bold mb-4">Scan & Boek</h2>

      {step === "select-order" && (
        <OrderSelectStep onSelect={handleOrderSelected} />
      )}

      {step === "scan" && selectedOrder && (
        <ScanStep
          order={selectedOrder}
          onBooked={handleBooked}
          onBack={reset}
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
    </div>
  );
}

/* ---------- Step 1: Select Active Order ---------- */

function OrderSelectStep({
  onSelect,
}: {
  onSelect: (order: Order) => void;
}) {
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
    </>
  );
}

/* ---------- Step 2: Camera Scan ---------- */

function ScanStep({
  order,
  onBooked,
  onBack,
}: {
  order: Order;
  onBooked: (booking: BookingResult) => void;
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
      const booking: BookingResult = await api.bookBox(blob, order.id);
      onBooked(booking);
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
  return (
    <>
      <div className="p-6 rounded-lg bg-green-600/20 border-2 border-green-600 text-center mb-4">
        <p className="text-green-400 text-2xl font-bold mb-2">
          Zet op rolcontainer
        </p>
        <p className="text-green-300 text-3xl font-black">
          {booking.rolcontainer}
        </p>
      </div>

      <Card className="p-4 mb-4">
        <div className="space-y-1">
          <p className="text-sm">
            <span className="text-muted-foreground">Product:</span>{" "}
            <span className="font-semibold">{booking.sku_name}</span>
          </p>
          <p className="text-sm">
            <span className="text-muted-foreground">SKU:</span>{" "}
            <span className="font-mono">{booking.sku_code}</span>
          </p>
          <p className="text-sm">
            <span className="text-muted-foreground">Order:</span>{" "}
            {booking.order_reference}
          </p>
          <p className="text-sm">
            <span className="text-muted-foreground">Klant:</span>{" "}
            {booking.klant}
          </p>
        </div>
      </Card>

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
