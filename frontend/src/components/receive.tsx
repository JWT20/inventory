import { useState, useEffect } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { useCamera } from "@/hooks/useCamera";
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

type Step = "scan" | "result" | "new-product";

export function ReceivePage() {
  const [step, setStep] = useState<Step>("scan");
  const [match, setMatch] = useState<MatchResult | null>(null);
  const [capturedBlob, setCapturedBlob] = useState<Blob | null>(null);

  function handleMatch(result: MatchResult | null, blob: Blob) {
    setCapturedBlob(blob);
    if (result) {
      setMatch(result);
      setStep("result");
    } else {
      setStep("new-product");
    }
  }

  function handleNewProductCreated() {
    toast.success("Product aangemaakt — klaar voor volgende scan");
    reset();
  }

  function rejectMatch() {
    setMatch(null);
    setStep("new-product");
  }

  function reset() {
    setStep("scan");
    setMatch(null);
    setCapturedBlob(null);
  }

  return (
    <div>
      <h2 className="text-xl font-bold mb-4">Scan</h2>

      {step === "scan" && <ScanStep onResult={handleMatch} />}

      {step === "result" && match && (
        <ResultStep match={match} onDone={reset} onReject={rejectMatch} />
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
  const { videoRef, canvasRef, capture: captureFrame } = useCamera();

  async function handleCapture() {
    setScanning(true);
    const blob = await captureFrame();
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
        onClick={handleCapture}
        disabled={scanning}
      >
        {scanning ? "Herkennen..." : "Scan"}
      </Button>
    </>
  );
}

/* ---------- Step 2: Match Result ---------- */

function ResultStep({
  match,
  onDone,
  onReject,
}: {
  match: MatchResult;
  onDone: () => void;
  onReject: () => void;
}) {
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

      <div className="flex flex-col gap-3">
        <Button size="lg" className="w-full h-14 text-lg" onClick={onDone}>
          Volgende doos scannen
        </Button>
        <button
          onClick={onReject}
          className="text-sm text-muted-foreground underline"
        >
          Niet correct? Nieuw product aanmaken
        </button>
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
  onCreated: () => void;
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
      await api.createNewProduct(
        blob,
        skuCode,
        name,
        description || undefined,
      );
      onCreated();
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
            placeholder="bijv. Chateau Margaux 2018"
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
