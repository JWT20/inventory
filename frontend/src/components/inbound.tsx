import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown } from "lucide-react";
import { toast } from "@/App";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface ExtractedLine {
  supplier_code: string;
  description: string;
  // In besteleenheden van de gematchte SKU: dozen, of flessen als is_bottle.
  quantity_boxes: number;
  quantity: number;
  quantity_unit: "boxes" | "pieces" | "unknown";
  confidence: number;
  matched_sku_id: number | null;
  matched_sku_code: string | null;
  matched_sku_name: string | null;
  is_bottle: boolean;
}

const BOTTLES_PER_BOX = 6;

// Mirrors backend _resolve_inbound_quantity for interactive (her)koppelen in
// de preview: een fles-SKU telt pieces 1-op-1 en kan dozen/onbekend niet zelf
// omrekenen (0 → operator vult het aantal flessen in); een doos-SKU houdt de
// bestaande deling door BOTTLES_PER_BOX.
function resolveQuantityForUnit(
  quantity: number,
  unit: ExtractedLine["quantity_unit"],
  isBottle: boolean,
): number {
  if (quantity <= 0) return 0;
  if (isBottle) return unit === "pieces" ? quantity : 0;
  if (unit === "boxes") return quantity;
  if (unit === "pieces") return Math.floor(quantity / BOTTLES_PER_BOX);
  return 0;
}

function extractedLabel(line: ExtractedLine): string {
  if (line.quantity <= 0) return "—";
  if (line.quantity_unit === "pieces") return `${line.quantity} flessen`;
  if (line.quantity_unit === "boxes") return `${line.quantity} dozen`;
  return `${line.quantity} (eenheid onbekend)`;
}

function mismatchReason(line: ExtractedLine): string | null {
  if (line.is_bottle) {
    if (line.quantity_unit === "pieces" && line.quantity > 0) {
      if (line.quantity_boxes !== line.quantity) return "handmatig aangepast";
      return null;
    }
    if (line.quantity > 0) {
      return "fles-product: eenheid onduidelijk — voer het aantal flessen in";
    }
    return null;
  }
  if (line.quantity_unit === "unknown" && line.quantity > 0) {
    return "eenheid onbekend — controleer";
  }
  if (line.quantity_unit === "pieces" && line.quantity > 0) {
    if (line.quantity < BOTTLES_PER_BOX) return "partieel, genegeerd";
    const remainder = line.quantity % BOTTLES_PER_BOX;
    const autoBoxes = Math.floor(line.quantity / BOTTLES_PER_BOX);
    if (line.quantity_boxes !== autoBoxes) return "handmatig aangepast";
    if (remainder > 0) return `rest ${remainder} fl genegeerd`;
    return null;
  }
  if (line.quantity_unit === "boxes" && line.quantity > 0 && line.quantity !== line.quantity_boxes) {
    return "handmatig aangepast";
  }
  return null;
}

interface SKUOption {
  id: number;
  sku_code: string;
  name: string;
  active: boolean;
  supplier_name: string | null;
  is_bottle: boolean;
}

/**
 * Zoekbare, scrollbare keuzelijst voor het koppelen van een bestaande SKU.
 * Toont de volledige lijst (scrollbaar) en filtert client-side op naam,
 * SKU-code of leverancier, zodat ook SKU's verderop in het alfabet — bijv.
 * wijnen met de T — snel te vinden zijn.
 *
 * Het menu wordt via een portal (fixed) gerenderd, zodat het niet door de
 * scroll-container van de regellijst wordt afgekapt en het niet als
 * geneste <button> in de regel terechtkomt.
 */
function SkuCombobox({
  options,
  value,
  onChange,
  placeholder = "Kies bestaande SKU...",
}: {
  options: SKUOption[];
  value: number | null;
  onChange: (id: number) => void;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [menuPos, setMenuPos] = useState<{ left: number; top: number; width: number } | null>(null);

  const selected = options.find((o) => o.id === value) ?? null;

  const q = query.trim().toLowerCase();
  const filtered = q
    ? options.filter(
        (o) =>
          o.name.toLowerCase().includes(q) ||
          o.sku_code.toLowerCase().includes(q) ||
          (o.supplier_name?.toLowerCase().includes(q) ?? false),
      )
    : options;

  const updatePosition = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setMenuPos({ left: r.left, top: r.bottom + 4, width: r.width });
  }, []);

  useEffect(() => {
    if (!open) return;
    updatePosition();
    function onDocClick(e: MouseEvent) {
      const t = e.target as Node;
      if (triggerRef.current?.contains(t) || menuRef.current?.contains(t)) return;
      setOpen(false);
    }
    function reposition() {
      updatePosition();
    }
    document.addEventListener("mousedown", onDocClick);
    // capture=true so we also catch scrolling of the regellijst container.
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      window.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
    };
  }, [open, updatePosition]);

  return (
    <div className="relative flex-1">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex h-8 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-xs ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
      >
        <span className={cn("line-clamp-1 text-left", !selected && "text-muted-foreground")}>
          {selected ? `${selected.sku_code} - ${selected.name}` : placeholder}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 opacity-50" />
      </button>
      {open &&
        menuPos &&
        createPortal(
          <div
            ref={menuRef}
            style={{ left: menuPos.left, top: menuPos.top, width: menuPos.width }}
            className="fixed z-50 rounded-md border border-border bg-card text-foreground shadow-md"
          >
            <div className="p-1">
              <Input
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Zoek op naam, code of leverancier..."
                className="h-8 text-xs"
              />
            </div>
            <div className="max-h-60 overflow-y-auto p-1">
              {filtered.length === 0 ? (
                <div className="px-2 py-1.5 text-xs text-muted-foreground">
                  Geen SKU gevonden
                </div>
              ) : (
                filtered.map((sku) => (
                  <button
                    key={sku.id}
                    type="button"
                    onClick={() => {
                      onChange(sku.id);
                      setOpen(false);
                      setQuery("");
                    }}
                    className={cn(
                      "flex w-full flex-col items-start rounded-sm px-2 py-1.5 text-left text-xs hover:bg-accent hover:text-accent-foreground",
                      sku.id === value && "bg-accent/50",
                    )}
                  >
                    <span className="line-clamp-1">
                      {sku.sku_code} - {sku.name}
                      {sku.is_bottle ? " · fles" : ""}
                    </span>
                    {sku.supplier_name && (
                      <span className="text-[10px] text-muted-foreground">
                        {sku.supplier_name}
                      </span>
                    )}
                  </button>
                ))
              )}
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}

interface ExtractPreview {
  supplier_name: string;
  reference: string;
  document_type: string;
  lines: ExtractedLine[];
  image_url: string;
  raw_text: string;
  document_sha256?: string | null;
  duplicate_of_shipment_id?: number | null;
  duplicate_of_status?: string | null;
}

interface DuplicatePakbonDetail {
  code: "duplicate_pakbon";
  message: string;
  existing_shipment_id: number | null;
  existing_status: string | null;
}

function isDuplicatePakbonError(err: unknown): err is ApiError & { detail: DuplicatePakbonDetail } {
  return (
    err instanceof ApiError &&
    err.status === 409 &&
    typeof err.detail === "object" &&
    err.detail !== null &&
    (err.detail as { code?: string }).code === "duplicate_pakbon"
  );
}

export function InboundPage() {
  const [loading, setLoading] = useState(false);
  const [confirmingInbound, setConfirmingInbound] = useState(false);
  const [preview, setPreview] = useState<ExtractPreview | null>(null);
  const [selectedLineIndex, setSelectedLineIndex] = useState<number | null>(null);
  const [supplierName, setSupplierName] = useState("");
  const [documentType, setDocumentType] = useState<"pakbon" | "invoice" | "unknown">("unknown");
  const [skuOptions, setSkuOptions] = useState<SKUOption[]>([]);
  const [selectedSkuByLine, setSelectedSkuByLine] = useState<Record<number, number>>({});
  const [inputMode, setInputMode] = useState<"file" | "text">("file");
  const [pasteText, setPasteText] = useState("");
  const [ignoredLines, setIgnoredLines] = useState<Set<number>>(new Set());
  const [editingLines, setEditingLines] = useState<Set<number>>(new Set());

  useEffect(() => {
    async function loadSkus() {
      try {
        // Haal de volledige actieve lijst op (niet de standaard 100) zodat ook
        // SKU's verderop in het alfabet — bijv. wijnen met de T — beschikbaar
        // zijn om te scrollen en doorzoeken in de koppel-combobox.
        const skus = await api.listSKUs(true, undefined, { limit: 10000 });
        setSkuOptions((skus || []) as SKUOption[]);
      } catch (err: unknown) {
        toast.error(err instanceof Error ? err.message : "SKU's laden mislukt");
      }
    }
    void loadSkus();
  }, []);

  function applyPreview(data: ExtractPreview) {
    setPreview(data);
    setSelectedLineIndex(null);
    setSelectedSkuByLine({});
    setIgnoredLines(new Set());
    setEditingLines(new Set());
    toast.success("Extractie voltooid");
  }

  function toggleIgnoreLine(lineIndex: number) {
    setIgnoredLines((prev) => {
      const next = new Set(prev);
      if (next.has(lineIndex)) next.delete(lineIndex);
      else next.add(lineIndex);
      return next;
    });
  }

  function setEditing(lineIndex: number, open: boolean) {
    setEditingLines((prev) => {
      const next = new Set(prev);
      if (open) next.add(lineIndex);
      else next.delete(lineIndex);
      return next;
    });
  }

  function openEdit(lineIndex: number) {
    if (!preview) return;
    const current = preview.lines[lineIndex]?.matched_sku_id;
    if (current) {
      setSelectedSkuByLine((prev) => ({ ...prev, [lineIndex]: current }));
    }
    setEditing(lineIndex, true);
  }

  async function unlinkLine(lineIndex: number) {
    if (!preview) return;
    const line = preview.lines[lineIndex];
    const supplierName = (preview.supplier_name || "").trim();
    const supplierCode = (line.supplier_code || "").trim();

    // Remove the stored supplier→SKU mapping so this code no longer auto-matches.
    if (supplierName && supplierCode) {
      try {
        const mappings = (await api.listSupplierMappings(supplierName)) as {
          id: number;
          supplier_code: string;
        }[];
        const match = (mappings || []).find(
          (m) => m.supplier_code.toUpperCase() === supplierCode.toUpperCase(),
        );
        if (match) await api.deleteSupplierMapping(match.id);
      } catch (err: unknown) {
        toast.error(
          err instanceof Error
            ? `Koppeling op scherm verwijderd, maar opgeslagen koppeling niet: ${err.message}`
            : "Opgeslagen koppeling kon niet worden verwijderd",
        );
      }
    }

    setPreview((prev) => {
      if (!prev) return prev;
      const nextLines = [...prev.lines];
      const current = nextLines[lineIndex];
      nextLines[lineIndex] = {
        ...current,
        matched_sku_id: null,
        matched_sku_code: null,
        matched_sku_name: null,
        is_bottle: false,
        // Een fles-telling is zonder fles-SKU niet meer geldig; val terug op
        // de doos-interpretatie tot er opnieuw gekoppeld wordt.
        quantity_boxes: current.is_bottle
          ? resolveQuantityForUnit(current.quantity, current.quantity_unit, false)
          : current.quantity_boxes,
      };
      return { ...prev, lines: nextLines };
    });
    setSelectedSkuByLine((prev) => {
      const next = { ...prev };
      delete next[lineIndex];
      return next;
    });
    setEditing(lineIndex, false);
    toast.success("Ontkoppeld — kies een nieuw SKU of zet de regel op niet boeken");
  }

  async function extractFromFile(file: File) {
    setLoading(true);
    try {
      const data = await api.extractShipmentPreview(
        file,
        supplierName,
        documentType,
        file.name,
      );
      applyPreview(data);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Extractie mislukt");
    } finally {
      setLoading(false);
    }
  }

  async function extractFromText() {
    const text = pasteText.trim();
    if (!text) {
      toast.error("Plak eerst de besteltekst.");
      return;
    }
    setLoading(true);
    try {
      const data = await api.extractShipmentPreviewText(text, supplierName, documentType);
      applyPreview(data);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Extractie mislukt");
    } finally {
      setLoading(false);
    }
  }

  async function confirmInbound() {
    if (!preview) return;
    const active = preview.lines
      .map((line, idx) => ({ line, idx }))
      .filter(({ idx }) => !ignoredLines.has(idx));

    const lines = active
      .filter(({ line }) => line.matched_sku_id && line.quantity_boxes > 0)
      .map(({ line }) => ({
        sku_id: line.matched_sku_id as number,
        quantity: line.quantity_boxes,
        supplier_code: line.supplier_code || null,
      }));

    // Every non-ignored line must be bookable (matched SKU + at least 1 box).
    // Anything else needs an explicit decision: resolve it or mark "niet boeken".
    const needsDecision = active.filter(
      ({ line }) => !line.matched_sku_id || line.quantity_boxes <= 0,
    );
    if (needsDecision.length > 0) {
      const codes = needsDecision.map(({ line }) => line.supplier_code || "(geen code)").join(", ");
      toast.error(
        `Nog niet boekbaar: ${codes}. Koppel een SKU en zet een aantal, of zet de regel op "niet boeken".`,
      );
      return;
    }
    if (lines.length === 0) {
      toast.error("Geen boekbare regels gevonden.");
      return;
    }

    setConfirmingInbound(true);
    try {
      let created: { id: number };
      try {
        created = await api.createShipment({
          supplier_name: preview.supplier_name || null,
          reference: preview.reference || null,
          document_sha256: preview.document_sha256 ?? null,
          lines,
        });
      } catch (err: unknown) {
        if (isDuplicatePakbonError(err)) {
          const existingId = err.detail.existing_shipment_id;
          const status = err.detail.existing_status;
          const choice = window.confirm(
            `${err.detail.message}\n\n` +
              (existingId
                ? `Bestaande pakbon #${existingId} (status: ${status ?? "?"}).\n\n`
                : "") +
              "OK = toch nieuwe aanmaken (krijgt achtervoegsel -dup-N)\n" +
              "Annuleren = afbreken",
          );
          if (!choice) {
            toast.error("Inbound afgebroken — pakbon bestaat al.");
            return;
          }
          created = await api.createShipment({
            supplier_name: preview.supplier_name || null,
            reference: preview.reference || null,
            document_sha256: preview.document_sha256 ?? null,
            force: true,
            lines,
          });
        } else {
          throw err;
        }
      }
      await api.bookShipment(created.id);
      toast.success(`Inbound geboekt (pakbon #${created.id})`);
      setPreview(null);
      setSelectedLineIndex(null);
      setSelectedSkuByLine({});
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Inbound boeken mislukt");
    } finally {
      setConfirmingInbound(false);
    }
  }


  async function linkExistingSku(lineIndex: number) {
    if (!preview) return;
    const selectedSkuId = selectedSkuByLine[lineIndex];
    const sku = skuOptions.find((s) => s.id === selectedSkuId);
    if (!sku) {
      toast.error("Kies eerst een bestaande SKU");
      return;
    }

    const line = preview.lines[lineIndex];
    const supplierNameForMapping = (preview.supplier_name || "").trim();
    const supplierCodeForMapping = (line.supplier_code || "").trim();
    let mappingPersisted = false;
    let persistenceSkippedReason: string | null = null;

    if (!supplierNameForMapping || !supplierCodeForMapping) {
      persistenceSkippedReason = "leverancier of supplier code ontbreekt";
    } else {
      try {
        await api.confirmLineMatch({
          supplier_name: supplierNameForMapping,
          supplier_code: supplierCodeForMapping,
          chosen_sku_id: sku.id,
          persist_mapping: true,
        });
        mappingPersisted = true;
      } catch (err: unknown) {
        persistenceSkippedReason =
          err instanceof Error ? err.message : "koppeling opslaan mislukt";
      }
    }

    // Een andere besteleenheid (doos ↔ fles) maakt de eerdere omrekening
    // ongeldig: herbereken dan met de eenheid van de gekozen SKU. Bij een
    // gelijkblijvende eenheid blijft een handmatig aangepast aantal staan.
    const unitChanged = line.is_bottle !== sku.is_bottle;
    const resolvedQty = resolveQuantityForUnit(
      line.quantity,
      line.quantity_unit,
      sku.is_bottle,
    );

    setPreview((prev) => {
      if (!prev) return prev;
      const nextLines = [...prev.lines];
      nextLines[lineIndex] = {
        ...nextLines[lineIndex],
        matched_sku_id: sku.id,
        matched_sku_code: sku.sku_code,
        matched_sku_name: sku.name,
        is_bottle: sku.is_bottle,
        quantity_boxes: unitChanged ? resolvedQty : nextLines[lineIndex].quantity_boxes,
      };
      return { ...prev, lines: nextLines };
    });
    setEditing(lineIndex, false);
    if (sku.is_bottle && unitChanged && resolvedQty === 0 && line.quantity > 0) {
      toast(
        `Gekoppeld aan ${sku.sku_code} — fles-product, maar de pakbon noemt geen flessen-aantal. Voer het aantal flessen handmatig in.`,
      );
    } else if (sku.is_bottle && unitChanged) {
      toast.success(
        `Gekoppeld aan ${sku.sku_code} — fles-product: aantal herberekend naar ${resolvedQty} flessen${mappingPersisted ? " (onthouden voor volgende pakbonnen)" : ""}`,
      );
    } else if (mappingPersisted) {
      toast.success(`Gekoppeld aan ${sku.sku_code} (onthouden voor volgende pakbonnen)`);
    } else {
      toast.success(`Gekoppeld aan ${sku.sku_code} (niet onthouden: ${persistenceSkippedReason})`);
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
      const created = await api.createConceptProduct(
        supplierCode,
        line.description || undefined,
      );

      const supplierNameForMapping = (preview.supplier_name || "").trim();
      let mappingPersisted = false;
      if (supplierNameForMapping) {
        try {
          await api.confirmLineMatch({
            supplier_name: supplierNameForMapping,
            supplier_code: supplierCode,
            chosen_sku_id: created.id,
            persist_mapping: true,
          });
          mappingPersisted = true;
        } catch (err: unknown) {
          toast.error(
            err instanceof Error
              ? `Concept aangemaakt, maar koppeling niet opgeslagen: ${err.message}`
              : "Concept aangemaakt, maar koppeling niet opgeslagen",
          );
        }
      }

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
      setEditing(lineIndex, false);
      if (mappingPersisted) {
        toast.success(`Concept product ${created.sku_code} aangemaakt en gekoppeld`);
      } else if (!supplierNameForMapping) {
        toast.success(
          `Concept product ${created.sku_code} aangemaakt — vul een leverancier in om de koppeling voor volgende pakbonnen te onthouden`,
        );
      } else {
        toast.success(`Concept product ${created.sku_code} aangemaakt`);
      }
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Concept product aanmaken mislukt");
    }
  }

  function updateLineQuantity(lineIndex: number, newQty: number) {
    setPreview((prev) => {
      if (!prev) return prev;
      const nextLines = [...prev.lines];
      nextLines[lineIndex] = { ...nextLines[lineIndex], quantity_boxes: Math.max(0, newQty) };
      return { ...prev, lines: nextLines };
    });
  }

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-bold">Inbound pakbon/factuur</h2>

      <Card className="p-3 space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <Input
            className="text-sm"
            placeholder="Leverancier (optioneel)"
            value={supplierName}
            onChange={(e) => setSupplierName(e.target.value)}
          />
          <Select
            value={documentType}
            onValueChange={(v) => setDocumentType(v as "pakbon" | "invoice" | "unknown")}
          >
            <SelectTrigger className="text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="unknown">Auto detect</SelectItem>
              <SelectItem value="pakbon">Pakbon</SelectItem>
              <SelectItem value="invoice">Factuur</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="inline-flex rounded-md border border-border p-0.5 text-sm">
          <button
            type="button"
            className={`px-3 py-1 rounded ${inputMode === "file" ? "bg-primary text-primary-foreground" : ""}`}
            onClick={() => setInputMode("file")}
          >
            Bestand uploaden
          </button>
          <button
            type="button"
            className={`px-3 py-1 rounded ${inputMode === "text" ? "bg-primary text-primary-foreground" : ""}`}
            onClick={() => setInputMode("text")}
          >
            Tekst plakken
          </button>
        </div>

        {inputMode === "file" ? (
          <label className="block">
            <input
              type="file"
              className="hidden"
              accept="application/pdf,image/*"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (file) void extractFromFile(file);
              }}
            />
            <span className="inline-flex w-full items-center justify-center rounded-md border border-border py-3 text-sm font-medium cursor-pointer">
              {loading ? "Bezig..." : "Kies PDF of afbeelding"}
            </span>
          </label>
        ) : (
          <div className="space-y-2">
            <textarea
              className="w-full min-h-[160px] rounded-md border border-border bg-background p-2 text-sm font-mono"
              placeholder={"Plak hier je bestelregels, bijv.:\n0009532  Vinho Verde Alvarinho 2024  6  € 7,31  € 43,86"}
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
            />
            <Button onClick={extractFromText} disabled={loading} className="w-full">
              {loading ? "Bezig..." : "Tekst verwerken"}
            </Button>
          </div>
        )}
      </Card>

      {preview && (
        <div className="grid md:grid-cols-2 gap-4">
          <Card className="p-3">
            {preview.duplicate_of_shipment_id && (
              <div
                role="alert"
                className="mb-3 rounded-md border border-amber-300 bg-amber-50 p-2 text-sm text-amber-900"
              >
                ⚠ Dit document is eerder geüpload als pakbon #
                {preview.duplicate_of_shipment_id}
                {preview.duplicate_of_status
                  ? ` (status: ${preview.duplicate_of_status})`
                  : ""}
                . Controleer of dit echt een nieuwe pakbon is voor je doorgaat.
              </div>
            )}
            <p className="text-sm"><strong>Leverancier:</strong> {preview.supplier_name || "-"}</p>
            <p className="text-sm"><strong>Referentie:</strong> {preview.reference || "-"}</p>
            <p className="text-sm"><strong>Type:</strong> {preview.document_type || "unknown"}</p>
            <p className="text-xs text-muted-foreground mt-2">
              Auto-mapping: eerst op leverancier + supplier code (opgeslagen mappings), daarna op exacte SKU-code match.
            </p>

            {preview.image_url && (
              <div className="mt-3 border border-border rounded overflow-hidden">
                <img src={preview.image_url} alt="Pakbon/factuur" className="w-full" />
              </div>
            )}
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
                {preview.lines.map((line, idx) => {
                  const reason = mismatchReason(line);
                  const unknownUnit = line.quantity_unit === "unknown";
                  const ignored = ignoredLines.has(idx);
                  const editing = editingLines.has(idx);
                  const borderClass = selectedLineIndex === idx
                    ? "border-primary"
                    : ignored
                      ? "border-border"
                      : reason
                        ? "border-border border-l-2 border-l-amber-500"
                        : "border-border";
                  return (
                  <div
                    key={`${line.supplier_code}-${idx}`}
                    role="button"
                    tabIndex={0}
                    className={`w-full text-left border rounded p-2 ${borderClass} ${ignored ? "opacity-60" : ""}`}
                    onClick={() => setSelectedLineIndex(idx)}
                    onKeyDown={(e) => {
                      if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
                        e.preventDefault();
                        setSelectedLineIndex(idx);
                      }
                    }}
                  >
                    <div className="flex items-center gap-2">
                      <p className={`text-sm font-medium ${ignored ? "line-through" : ""}`}>
                        {line.supplier_code || "(geen code)"}
                      </p>
                      {ignored && (
                        <span className="text-xs rounded bg-muted px-1.5 py-0.5 text-muted-foreground">
                          wordt niet geboekt
                        </span>
                      )}
                    </div>
                    <p className={`text-sm ${ignored ? "line-through" : ""}`}>{line.description || "-"}</p>
                    <div className="grid grid-cols-2 gap-2 mt-2">
                      <div>
                        <p className="text-xs text-muted-foreground">Uitgelezen</p>
                        <p
                          aria-label="Uitgelezen hoeveelheid"
                          className={`text-sm tabular-nums ${unknownUnit ? "font-semibold text-amber-600" : ""}`}
                        >
                          {extractedLabel(line)}
                        </p>
                      </div>
                      <div onClick={(e) => e.stopPropagation()}>
                        <p className="text-xs text-muted-foreground">Berekend</p>
                        <div className="inline-flex items-center gap-1 mt-0.5">
                          <button
                            type="button"
                            aria-label="Decrease boxes"
                            className="w-5 h-5 rounded bg-muted text-foreground text-xs font-bold flex items-center justify-center disabled:opacity-30 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring"
                            disabled={line.quantity_boxes <= 0}
                            onClick={() => updateLineQuantity(idx, line.quantity_boxes - 1)}
                          >
                            &minus;
                          </button>
                          <input
                            type="number"
                            min={0}
                            aria-label="Number of boxes"
                            className="w-12 text-center border border-border rounded px-1 py-0.5 text-xs bg-background tabular-nums [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                            value={line.quantity_boxes}
                            onChange={(e) => updateLineQuantity(idx, parseInt(e.target.value, 10) || 0)}
                          />
                          <button
                            type="button"
                            aria-label="Increase boxes"
                            className="w-5 h-5 rounded bg-muted text-foreground text-xs font-bold flex items-center justify-center focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring"
                            onClick={() => updateLineQuantity(idx, line.quantity_boxes + 1)}
                          >
                            +
                          </button>
                          <span className="text-sm ml-1">{line.is_bottle ? "flessen" : "dozen"}</span>
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 text-xs mt-1">
                      {reason && (
                        <span className={unknownUnit ? "text-amber-600" : "text-muted-foreground"}>
                          ⚠ {reason}
                        </span>
                      )}
                      <span className="text-muted-foreground ml-auto">
                        Confidence: {(line.confidence * 100).toFixed(0)}%
                      </span>
                    </div>
                    <div className="flex items-center gap-2 mt-1">
                      <p className="text-xs">
                        {line.matched_sku_code
                          ? `Match: ${line.matched_sku_code} - ${line.matched_sku_name}${line.is_bottle ? " · fles" : ""}`
                          : "Geen SKU-match"}
                      </p>
                      {line.matched_sku_code && !ignored && !editing && (
                        <div className="ml-auto flex gap-1" onClick={(e) => e.stopPropagation()}>
                          <Button
                            type="button"
                            variant="ghost"
                            className="h-6 text-xs"
                            onClick={() => openEdit(idx)}
                          >
                            Wijzig koppeling
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            className="h-6 text-xs text-destructive"
                            onClick={() => {
                              void unlinkLine(idx);
                            }}
                          >
                            Ontkoppel
                          </Button>
                        </div>
                      )}
                    </div>
                    {(!line.matched_sku_code || editing) && !ignored && (
                      <div className="mt-2 space-y-2" onClick={(e) => e.stopPropagation()}>
                        <div className="flex gap-2">
                          <SkuCombobox
                            options={skuOptions}
                            value={selectedSkuByLine[idx] ?? null}
                            onChange={(id) =>
                              setSelectedSkuByLine((prev) => ({
                                ...prev,
                                [idx]: id,
                              }))
                            }
                          />
                          <Button
                            type="button"
                            variant="outline"
                            onClick={() => {
                              void linkExistingSku(idx);
                            }}
                          >
                            Koppel
                          </Button>
                        </div>
                        <Button
                          type="button"
                          variant="outline"
                          onClick={() => {
                            void createConceptForLine(idx);
                          }}
                        >
                          Concept product
                        </Button>
                        {editing && (
                          <Button
                            type="button"
                            variant="ghost"
                            className="h-7 text-xs text-muted-foreground"
                            onClick={() => setEditing(idx, false)}
                          >
                            Annuleer
                          </Button>
                        )}
                      </div>
                    )}
                    <div className="mt-2" onClick={(e) => e.stopPropagation()}>
                      <Button
                        type="button"
                        variant="ghost"
                        className="h-7 text-xs text-muted-foreground"
                        onClick={() => toggleIgnoreLine(idx)}
                      >
                        {ignored ? "Wel boeken" : "Niet boeken"}
                      </Button>
                    </div>
                  </div>
                  );
                })}
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
