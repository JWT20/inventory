import { useState, useEffect, useRef } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ScanModeToggle } from "./ScanModeToggle";
import { SCAN_MODE_WORD } from "./constants";
import type { IdentifyResult, ScanMode } from "./types";

export function IdentifyScanStep({
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
