import { useEffect, useRef, useState } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

interface BBox {
  x: number;
  y: number;
  width: number;
  height: number;
  page: number;
}

interface ExtractedLine {
  supplier_code: string;
  description: string;
  quantity_boxes: number;
  confidence: number;
  bbox: BBox | null;
  matched_sku_id: number | null;
  matched_sku_code: string | null;
  matched_sku_name: string | null;
}

interface ExtractPreview {
  supplier_name: string;
  reference: string;
  document_type: string;
  lines: ExtractedLine[];
  image_url: string;
  raw_text: string;
}

export function InboundPage() {
  const [loading, setLoading] = useState(false);
  const [confirmingInbound, setConfirmingInbound] = useState(false);
  const [preview, setPreview] = useState<ExtractPreview | null>(null);
  const [selectedLineIndex, setSelectedLineIndex] = useState<number | null>(null);
  const [supplierName, setSupplierName] = useState("");
  const [documentType, setDocumentType] = useState<"pakbon" | "invoice" | "unknown">("unknown");

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

  async function extractFromBlob(blob: Blob) {
    setLoading(true);
    try {
      const data = await api.extractShipmentPreview(blob, supplierName, documentType);
      setPreview(data);
      setSelectedLineIndex(null);
      toast.success("Extractie voltooid");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Extractie mislukt");
    } finally {
      setLoading(false);
    }
  }

  async function capturePhoto() {
    if (!videoRef.current || !canvasRef.current) return;

    const canvas = canvasRef.current;
    canvas.width = videoRef.current.videoWidth;
    canvas.height = videoRef.current.videoHeight;
    canvas.getContext("2d")!.drawImage(videoRef.current, 0, 0);

    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.85),
    );
    if (!blob) return;
    await extractFromBlob(blob);
  }

  async function uploadFallback(file: File) {
    await extractFromBlob(file);
  }

  async function confirmInbound() {
    if (!preview) return;
    const lines = preview.lines
      .filter((line) => line.matched_sku_id && line.quantity_boxes > 0)
      .map((line) => ({
        sku_id: line.matched_sku_id as number,
        quantity: line.quantity_boxes,
        supplier_code: line.supplier_code || null,
      }));

    const unmapped = preview.lines.filter((line) => !line.matched_sku_id && line.quantity_boxes > 0);
    if (unmapped.length > 0) {
      const codes = unmapped.map((l) => l.supplier_code || "(geen code)").join(", ");
      toast.error(`Nog geen SKU-match voor: ${codes}. Maak eerst SKU's aan bij deze regels.`);
      return;
    }
    if (lines.length === 0) {
      toast.error("Geen boekbare regels gevonden.");
      return;
    }

    setConfirmingInbound(true);
    try {
      const created = await api.createShipment({
        supplier_name: preview.supplier_name || null,
        reference: preview.reference || null,
        lines,
      });
      await api.bookShipment(created.id);
      toast.success(`Inbound geboekt (pakbon #${created.id})`);
      setPreview(null);
      setSelectedLineIndex(null);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Inbound boeken mislukt");
    } finally {
      setConfirmingInbound(false);
    }
  }

  async function createConceptForLine(lineIndex: number) {
    if (!preview) return;
    const line = preview.lines[lineIndex];
    if (!line || line.matched_sku_id) return;

    const supplierCode = (line.supplier_code || "").trim().toUpperCase();
    if (!supplierCode) {
      toast.error("Geen supplier code gevonden voor deze regel.");
      return;
    }

    try {
      const created = await api.createConceptProduct(supplierCode, line.description || undefined);

      setPreview((prev) => {
        if (!prev) return prev;
        const nextLines = [...prev.lines];
        nextLines[lineIndex] = {
          ...nextLines[lineIndex],
          matched_sku_id: created.id,
          matched_sku_code: created.sku_code,
          matched_sku_name: created.name,
        };
        return { ...prev, lines: nextLines };
      });
      toast.success(`Concept product ${created.sku_code} aangemaakt`);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Concept product aanmaken mislukt");
    }
  }

  const selectedBox =
    selectedLineIndex != null && preview?.lines[selectedLineIndex]?.bbox
      ? preview.lines[selectedLineIndex].bbox
      : null;

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-bold">Inbound pakbon/factuur</h2>

      <Card className="p-3 space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <input
            className="border border-border rounded px-2 py-1 text-sm"
            placeholder="Leverancier (optioneel)"
            value={supplierName}
            onChange={(e) => setSupplierName(e.target.value)}
          />
          <select
            className="border border-border rounded px-2 py-1 text-sm"
            value={documentType}
            onChange={(e) => setDocumentType(e.target.value as "pakbon" | "invoice" | "unknown")}
          >
            <option value="unknown">Auto detect</option>
            <option value="pakbon">Pakbon</option>
            <option value="invoice">Factuur</option>
          </select>
        </div>

        <div className="rounded-md overflow-hidden border border-border bg-black/5">
          <video ref={videoRef} className="w-full max-h-[320px] object-cover" muted playsInline />
        </div>
        <canvas ref={canvasRef} className="hidden" />

        <div className="flex gap-2">
          <Button onClick={capturePhoto} disabled={loading} className="flex-1">
            {loading ? "Bezig..." : "Maak foto"}
          </Button>
          <label className="flex-1">
            <input
              type="file"
              className="hidden"
              accept="image/*"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void uploadFallback(file);
              }}
            />
            <span className="inline-flex w-full items-center justify-center rounded-md border border-border py-2 text-sm font-medium">
              Upload fallback
            </span>
          </label>
        </div>
      </Card>

      {preview && (
        <div className="grid md:grid-cols-2 gap-4">
          <Card className="p-3">
            <p className="text-sm"><strong>Leverancier:</strong> {preview.supplier_name || "-"}</p>
            <p className="text-sm"><strong>Referentie:</strong> {preview.reference || "-"}</p>
            <p className="text-sm"><strong>Type:</strong> {preview.document_type || "unknown"}</p>

            <div className="relative mt-3 border border-border rounded overflow-hidden">
              <img src={preview.image_url} alt="Pakbon/factuur" className="w-full" />
              {selectedBox && (
                <div
                  className="absolute border-2 border-red-500 bg-red-500/10"
                  style={{
                    left: `${selectedBox.x * 100}%`,
                    top: `${selectedBox.y * 100}%`,
                    width: `${selectedBox.width * 100}%`,
                    height: `${selectedBox.height * 100}%`,
                  }}
                />
              )}
            </div>
            <div className="mt-3">
              <Button onClick={confirmInbound} disabled={confirmingInbound} className="w-full">
                {confirmingInbound ? "Inbound boeken..." : "Confirm inbound"}
              </Button>
            </div>
          </Card>

          <Card className="p-3">
            <p className="font-semibold mb-2">Geëxtraheerde regels</p>
            {preview.lines.length === 0 ? (
              <p className="text-sm text-muted-foreground">Geen productregels gevonden.</p>
            ) : (
              <div className="space-y-2 max-h-[520px] overflow-auto">
                {preview.lines.map((line, idx) => (
                  <button
                    key={`${line.supplier_code}-${idx}`}
                    className={`w-full text-left border rounded p-2 ${selectedLineIndex === idx ? "border-primary" : "border-border"}`}
                    onClick={() => setSelectedLineIndex(idx)}
                  >
                    <p className="text-sm font-medium">{line.supplier_code || "(geen code)"}</p>
                    <p className="text-sm">{line.description || "-"}</p>
                    <p className="text-xs text-muted-foreground">
                      Boxes: {line.quantity_boxes} · Confidence: {(line.confidence * 100).toFixed(0)}%
                    </p>
                    <p className="text-xs mt-1">
                      {line.matched_sku_code
                        ? `Match: ${line.matched_sku_code} - ${line.matched_sku_name}`
                        : "Geen SKU-match"}
                    </p>
                    {!line.matched_sku_code && (
                      <Button
                        type="button"
                        className="mt-2"
                        variant="outline"
                        onClick={(e) => {
                          e.stopPropagation();
                          void createConceptForLine(idx);
                        }}
                      >
                        Concept product
                      </Button>
                    )}
                  </button>
                ))}
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
