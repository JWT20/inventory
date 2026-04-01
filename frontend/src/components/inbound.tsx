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
  const [preview, setPreview] = useState<ExtractPreview | null>(null);
  const [selectedLineIndex, setSelectedLineIndex] = useState<number | null>(null);
  const [supplierName, setSupplierName] = useState("");
  const [documentType, setDocumentType] = useState<"pakbon" | "invoice" | "unknown">("unknown");
<<<<<<< HEAD
  const [skuOptions, setSkuOptions] = useState<{ id: number; sku_code: string; name: string }[]>([]);
  const [lineSkuSelections, setLineSkuSelections] = useState<Record<number, number>>({});
  const [saveMappings, setSaveMappings] = useState(true);
  const [autoBook, setAutoBook] = useState(true);
=======
>>>>>>> 07c7d39 (Add inbound document extraction preview flow)

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

<<<<<<< HEAD
  useEffect(() => {
    async function loadSkus() {
      try {
        const skus = await api.listSKUs(false);
        setSkuOptions(skus);
      } catch {
        toast.error("Kan SKU-lijst niet laden");
      }
    }
    loadSkus();
  }, []);

=======
>>>>>>> 07c7d39 (Add inbound document extraction preview flow)
  async function extractFromBlob(blob: Blob) {
    setLoading(true);
    try {
      const data = await api.extractShipmentPreview(blob, supplierName, documentType);
      setPreview(data);
<<<<<<< HEAD
      setLineSkuSelections(
        Object.fromEntries(
          data.lines
            .map((line: ExtractedLine, idx: number) =>
              line.matched_sku_id ? [idx, line.matched_sku_id] : null)
            .filter(Boolean) as [number, number][],
        ),
      );
=======
>>>>>>> 07c7d39 (Add inbound document extraction preview flow)
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

  const selectedBox =
    selectedLineIndex != null && preview?.lines[selectedLineIndex]?.bbox
      ? preview.lines[selectedLineIndex].bbox
      : null;

<<<<<<< HEAD
  const unresolvedCount = preview
    ? preview.lines.filter((_, idx) => !lineSkuSelections[idx]).length
    : 0;

  async function confirmInbound() {
    if (!preview) return;
    if (unresolvedCount > 0) {
      toast.error("Koppel eerst alle regels aan een SKU");
      return;
    }
    try {
      const payload = {
        supplier_name: (preview.supplier_name || supplierName || "Onbekend").trim(),
        reference: preview.reference || undefined,
        save_mappings: saveMappings,
        auto_book: autoBook,
        lines: preview.lines.map((line, idx) => ({
          supplier_code: line.supplier_code || "",
          sku_id: lineSkuSelections[idx],
          quantity_boxes: Math.max(1, line.quantity_boxes || 1),
          description: line.description || "",
        })),
      };
      await api.confirmInboundFromPreview(payload);
      toast.success(autoBook ? "Inbound bevestigd en geboekt" : "Inbound bevestigd als draft");
      setPreview(null);
      setLineSkuSelections({});
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Inbound bevestigen mislukt");
    }
  }

  function imageKeyFromUrl(url: string): string {
    const marker = "/api/files/";
    const idx = url.indexOf(marker);
    if (idx === -1) return "";
    return url.slice(idx + marker.length);
  }

  async function saveUnmatchedQueue() {
    if (!preview) return;
    const unmatched = preview.lines
      .map((line, idx) => ({ line, idx }))
      .filter(({ idx }) => !lineSkuSelections[idx]);
    if (unmatched.length === 0) {
      toast.error("Er zijn geen unmatched regels");
      return;
    }
    try {
      await api.saveUnmatchedInbound({
        supplier_name: (preview.supplier_name || supplierName || "Onbekend").trim(),
        reference: preview.reference || undefined,
        document_type: preview.document_type || undefined,
        image_key: imageKeyFromUrl(preview.image_url),
        lines: unmatched.map(({ line }) => ({
          supplier_code: line.supplier_code || "",
          description: line.description || "",
          quantity_boxes: Math.max(1, line.quantity_boxes || 1),
          bbox: line.bbox,
        })),
      });
      toast.success(`${unmatched.length} unmatched regels opgeslagen voor handelaar`);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Opslaan unmatched mislukt");
    }
  }

=======
>>>>>>> 07c7d39 (Add inbound document extraction preview flow)
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
<<<<<<< HEAD
            <div className="mt-3 space-y-2 text-sm">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={saveMappings}
                  onChange={(e) => setSaveMappings(e.target.checked)}
                />
                Supplier codes opslaan als mapping
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={autoBook}
                  onChange={(e) => setAutoBook(e.target.checked)}
                />
                Direct voorraad boeken
              </label>
              <Button
                onClick={confirmInbound}
                disabled={unresolvedCount > 0}
                className="w-full"
              >
                Confirm inbound {unresolvedCount > 0 ? `(nog ${unresolvedCount} open)` : ""}
              </Button>
              {unresolvedCount > 0 && (
                <Button
                  onClick={saveUnmatchedQueue}
                  variant="secondary"
                  className="w-full"
                >
                  Sla unmatched op voor handelaar
                </Button>
              )}
            </div>
=======
>>>>>>> 07c7d39 (Add inbound document extraction preview flow)
          </Card>

          <Card className="p-3">
            <p className="font-semibold mb-2">Geëxtraheerde regels</p>
            {preview.lines.length === 0 ? (
              <p className="text-sm text-muted-foreground">Geen productregels gevonden.</p>
            ) : (
              <div className="space-y-2 max-h-[520px] overflow-auto">
                {preview.lines.map((line, idx) => (
<<<<<<< HEAD
                  <div
                    key={`${line.supplier_code}-${idx}`}
                    role="button"
                    tabIndex={0}
                    className={`w-full text-left border rounded p-2 cursor-pointer ${selectedLineIndex === idx ? "border-primary" : "border-border"}`}
                    onClick={() => setSelectedLineIndex(idx)}
                    onKeyDown={(e) => e.key === "Enter" && setSelectedLineIndex(idx)}
=======
                  <button
                    key={`${line.supplier_code}-${idx}`}
                    className={`w-full text-left border rounded p-2 ${selectedLineIndex === idx ? "border-primary" : "border-border"}`}
                    onClick={() => setSelectedLineIndex(idx)}
>>>>>>> 07c7d39 (Add inbound document extraction preview flow)
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
<<<<<<< HEAD
                    <div
                      className="mt-2"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <select
                        className="w-full border border-border rounded px-2 py-1 text-xs"
                        value={lineSkuSelections[idx] ?? ""}
                        onChange={(e) =>
                          setLineSkuSelections((prev) => ({
                            ...prev,
                            [idx]: Number(e.target.value),
                          }))
                        }
                      >
                        <option value="">Kies SKU...</option>
                        {skuOptions.map((sku) => (
                          <option key={sku.id} value={sku.id}>
                            {sku.sku_code} - {sku.name}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
=======
                  </button>
>>>>>>> 07c7d39 (Add inbound document extraction preview flow)
                ))}
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
