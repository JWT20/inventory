import { useState, useEffect, useRef, useCallback } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";

interface MatchResult {
  sku_id: number;
  sku_code: string;
  sku_name: string;
  confidence: number;
}

type Step = "scan" | "label" | "new-product";

export function ReceivePage() {
  const [step, setStep] = useState<Step>("scan");
  const [match, setMatch] = useState<MatchResult | null>(null);
  const [capturedBlob, setCapturedBlob] = useState<Blob | null>(null);

  function handleMatch(result: MatchResult | null, blob: Blob) {
    setCapturedBlob(blob);
    if (result) {
      setMatch(result);
      setStep("label");
    } else {
      setStep("new-product");
    }
  }

  function handleNewProductCreated(sku: { id: number; sku_code: string; name: string }) {
    setMatch({
      sku_id: sku.id,
      sku_code: sku.sku_code,
      sku_name: sku.name,
      confidence: 1.0,
    });
    setStep("label");
  }

  function reset() {
    setStep("scan");
    setMatch(null);
    setCapturedBlob(null);
  }

  return (
    <div>
      <h2 className="text-xl font-bold mb-4">Scan & Label</h2>

      {step === "scan" && <ScanStep onResult={handleMatch} />}

      {step === "label" && match && (
        <LabelStep match={match} onDone={reset} />
      )}

      {step === "new-product" && capturedBlob && (
        <NewProductStep
          blob={capturedBlob}
          onCreated={handleNewProductCreated}
          onBack={reset}
        />
      )}
    </div>
  );
}

/* ---------- Step 1: Camera Scan ---------- */

function ScanStep({
  onResult,
}: {
  onResult: (match: MatchResult | null, blob: Blob) => void;
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
      canvas.toBlob(resolve, "image/jpeg", 0.85),
    );
    if (!blob) {
      setScanning(false);
      return;
    }

    try {
      const result: MatchResult | null = await api.identifyBox(blob);
      onResult(result, blob);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Scanfout");
    } finally {
      setScanning(false);
    }
  }

  return (
    <>
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
    </>
  );
}

/* ---------- Step 2: Print Label ---------- */

function LabelStep({
  match,
  onDone,
}: {
  match: MatchResult;
  onDone: () => void;
}) {
  const [barcodeUrl, setBarcodeUrl] = useState<string | null>(null);

  const loadBarcode = useCallback(async () => {
    try {
      const url = await api.fetchBarcode(match.sku_id);
      setBarcodeUrl(url);
    } catch {
      /* barcode preview is non-critical */
    }
  }, [match.sku_id]);

  useEffect(() => {
    loadBarcode();
    return () => {
      setBarcodeUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
    };
  }, [loadBarcode]);

  async function openPrintLabel() {
    try {
      const html = await api.fetchLabelHtml(match.sku_id);
      const win = window.open("", "_blank");
      if (win) {
        win.document.write(html);
        win.document.close();
        win.addEventListener("load", () => win.print());
      }
    } catch {
      toast.error("Kan label niet laden");
    }
  }

  async function downloadZpl() {
    try {
      const blob = await api.fetchZpl(match.sku_id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${match.sku_code}.zpl`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("Kan ZPL niet downloaden");
    }
  }

  return (
    <>
      <Card className="p-4 mb-4">
        <div className="flex justify-between items-start mb-2">
          <div>
            <p className="text-lg font-bold">{match.sku_name}</p>
            <p className="text-sm text-muted-foreground">{match.sku_code}</p>
          </div>
          <Badge variant="active">
            {Math.round(match.confidence * 100)}% match
          </Badge>
        </div>
      </Card>

      <Card className="p-4 mb-4">
        <Label className="mb-2 block text-sm text-muted-foreground">
          Barcode label
        </Label>
        <div className="flex justify-center bg-white rounded-lg p-4 mb-3">
          {barcodeUrl ? (
            <img
              src={barcodeUrl}
              alt={`Barcode ${match.sku_code}`}
              className="max-w-full h-auto"
            />
          ) : (
            <p className="text-sm text-muted-foreground">Laden...</p>
          )}
        </div>
        <p className="text-center text-sm font-mono text-muted-foreground">
          {match.sku_code}
        </p>
      </Card>

      <div className="flex flex-col gap-3">
        <Button size="lg" className="w-full h-14 text-lg" onClick={openPrintLabel}>
          Label printen
        </Button>
        <Button
          variant="outline"
          onClick={downloadZpl}
          className="h-10 text-sm font-medium text-muted-foreground hover:text-foreground"
        >
          Download ZPL (Zebra printer)
        </Button>
        <Button variant="secondary" size="lg" className="w-full" onClick={onDone}>
          Volgende doos scannen
        </Button>
      </div>
    </>
  );
}

/* ---------- New Product Form ---------- */

function NewProductStep({
  blob,
  onCreated,
  onBack,
}: {
  blob: Blob;
  onCreated: (sku: { id: number; sku_code: string; name: string }) => void;
  onBack: () => void;
}) {
  const [skuCode, setSkuCode] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [previewUrl] = useState(() => URL.createObjectURL(blob));

  useEffect(() => {
    return () => URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      const sku = await api.createNewProduct(
        blob,
        skuCode,
        name,
        description || undefined,
      );
      toast.success(`Nieuw product "${name}" aangemaakt`);
      onCreated({ id: sku.id, sku_code: sku.sku_code, name: sku.name });
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout bij aanmaken");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <div className="p-4 rounded-lg bg-amber-600/20 border-2 border-amber-600 text-center mb-4">
        <p className="text-amber-500 font-bold">Product niet herkend</p>
        <p className="text-amber-400 text-sm mt-1">
          Maak een nieuw product aan met de gescande foto
        </p>
      </div>

      <div className="w-full aspect-[4/3] rounded-lg overflow-hidden bg-black mb-4">
        <img
          src={previewUrl}
          alt="Gescande foto"
          className="w-full h-full object-cover"
        />
      </div>

      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-2">
          <Label>SKU Code</Label>
          <Input
            value={skuCode}
            onChange={(e) => setSkuCode(e.target.value)}
            placeholder="bijv. WN-001"
            required
          />
        </div>
        <div className="space-y-2">
          <Label>Productnaam</Label>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="bijv. Château Margaux 2018"
            required
          />
        </div>
        <div className="space-y-2">
          <Label>Omschrijving (optioneel)</Label>
          <Input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Korte omschrijving..."
          />
        </div>
        <Button
          type="submit"
          size="lg"
          className="w-full h-14 text-lg"
          disabled={submitting}
        >
          {submitting ? "Aanmaken & verwerken..." : "Product aanmaken"}
        </Button>
        <button
          type="button"
          onClick={onBack}
          className="text-sm text-muted-foreground underline w-full text-center block"
        >
          Terug naar scanner
        </button>
      </form>
    </>
  );
}
