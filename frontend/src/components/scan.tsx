import { useState, useEffect, useRef, useCallback } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

interface OrderLine {
  id: number;
  sku_code: string;
  sku_name: string;
  quantity: number;
  picked_quantity: number;
  status: string;
}

interface Order {
  id: number;
  order_number: string;
  customer_name: string;
  status: string;
  lines: OrderLine[];
}

interface PickResult {
  correct: boolean;
  confidence: number;
  matched_sku_code: string | null;
  matched_sku_name: string | null;
  expected_sku_code: string;
  expected_sku_name: string;
  message: string;
}

export function ScanPage() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [picking, setPicking] = useState<{
    order: Order;
    lineIndex: number;
  } | null>(null);

  const loadOrders = useCallback(async () => {
    try {
      const all = await api.listOrders();
      setOrders(all.filter((o: Order) => o.status !== "completed"));
    } catch {
      toast.error("Kan orders niet laden");
    }
  }, []);

  useEffect(() => {
    loadOrders();
  }, [loadOrders]);

  async function startPicking(order: Order) {
    if (order.status === "pending") {
      await api.updateOrderStatus(order.id, "picking");
      order.status = "picking";
    }
    const lineIndex = order.lines.findIndex((l) => l.status !== "picked");
    if (lineIndex === -1) {
      toast("Alle items al gepickt!");
      return;
    }
    setPicking({ order, lineIndex });
  }

  function stopPicking() {
    setPicking(null);
    loadOrders();
  }

  if (picking) {
    return (
      <PickingView
        order={picking.order}
        lineIndex={picking.lineIndex}
        onStop={stopPicking}
      />
    );
  }

  return (
    <>
      <h2 className="text-xl font-bold mb-4">Selecteer order om te picken</h2>
      <div className="space-y-3">
        {orders.length === 0 ? (
          <p className="text-center text-muted-foreground py-10">
            Geen openstaande orders
          </p>
        ) : (
          orders.map((o) => {
            const total = o.lines.reduce((s, l) => s + l.quantity, 0);
            const picked = o.lines.reduce((s, l) => s + l.picked_quantity, 0);
            return (
              <Card
                key={o.id}
                className="p-4 cursor-pointer active:scale-[0.98] transition-transform"
                onClick={() => startPicking(o)}
              >
                <div className="flex justify-between items-center mb-1">
                  <span className="font-semibold">{o.order_number}</span>
                  <Badge variant={o.status as "pending" | "picking"}>
                    {o.status === "pending" ? "Open" : "Bezig"}
                  </Badge>
                </div>
                <p className="text-sm text-muted-foreground">
                  {o.customer_name} &bull; {picked}/{total} gepickt
                </p>
              </Card>
            );
          })
        )}
      </div>
    </>
  );
}

function PickingView({
  order: initialOrder,
  lineIndex: initialLineIndex,
  onStop,
}: {
  order: Order;
  lineIndex: number;
  onStop: () => void;
}) {
  const [order, setOrder] = useState(initialOrder);
  const [lineIndex, setLineIndex] = useState(initialLineIndex);
  const [result, setResult] = useState<PickResult | null>(null);
  const [scanning, setScanning] = useState(false);

  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const line = order.lines[lineIndex];
  const totalLines = order.lines.length;
  const pickedLines = order.lines.filter((l) => l.status === "picked").length;

  useEffect(() => {
    async function startCamera() {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 960 } },
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
      canvas.toBlob(resolve, "image/jpeg", 0.85)
    );
    if (!blob) {
      setScanning(false);
      return;
    }

    try {
      const res: PickResult = await api.validatePick(line.id, blob);
      setResult(res);

      if (res.correct) {
        const updated = await api.getOrder(order.id);
        setOrder(updated);
        const nextIdx = updated.lines.findIndex(
          (l: OrderLine) => l.status !== "picked"
        );
        if (nextIdx === -1) {
          toast.success("Order compleet!");
          setTimeout(onStop, 2000);
        }
      }
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Scanfout");
    } finally {
      setScanning(false);
    }
  }

  function nextItem() {
    const nextIdx = order.lines.findIndex((l) => l.status !== "picked");
    if (nextIdx !== -1) {
      setLineIndex(nextIdx);
      setResult(null);
    }
  }

  const hasNext = order.lines.some((l) => l.status !== "picked") && result?.correct;

  return (
    <div>
      <div className="mb-3">
        <p className="text-sm text-muted-foreground">
          <strong>{order.order_number}</strong> — {order.customer_name}
        </p>
      </div>

      <Card className="p-4 mb-3">
        <p className="text-lg font-bold">{line.sku_name}</p>
        <p className="text-sm text-muted-foreground">
          {line.sku_code} &bull; {line.picked_quantity}/{line.quantity} dozen
        </p>
      </Card>

      <p className="text-sm text-muted-foreground mb-3">
        Regel {lineIndex + 1} van {totalLines} ({pickedLines} klaar)
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

      {result && (
        <div
          className={`p-4 rounded-lg text-center font-semibold mb-3 ${
            result.correct
              ? "bg-green-600/20 border-2 border-green-600 text-green-500"
              : result.matched_sku_code
              ? "bg-red-600/20 border-2 border-red-600 text-red-500"
              : "bg-amber-600/20 border-2 border-amber-600 text-amber-500"
          }`}
        >
          {result.message}
        </div>
      )}

      <div className="flex flex-col gap-3 items-center">
        <Button
          size="lg"
          className="w-full text-lg h-14"
          onClick={capture}
          disabled={scanning}
        >
          {scanning ? "Herkennen..." : "Scan"}
        </Button>
        {hasNext && (
          <Button
            variant="secondary"
            size="lg"
            className="w-full"
            onClick={nextItem}
          >
            Volgende
          </Button>
        )}
        <button
          onClick={onStop}
          className="text-sm text-muted-foreground underline"
        >
          Stop picken
        </button>
      </div>
    </div>
  );
}
