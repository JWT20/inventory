import { useRef, useState } from "react";
import { AlertCircle, CheckCircle2, FileUp, Loader2 } from "lucide-react";
import { toast } from "@/App";
import {
  api,
  type AdviceProductLinkImportResult,
  type AdviceProductLinkMapping,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

type LinkFile = {
  version: number;
  mappings: AdviceProductLinkMapping[];
  source_report?: {
    missing_product_id_count?: number;
    feed_ready?: boolean;
  };
};

function parseLinkFile(value: unknown): LinkFile {
  if (!value || typeof value !== "object") {
    throw new Error("Dit is geen geldig koppelbestand");
  }
  const file = value as Partial<LinkFile>;
  if (file.version !== 1 || !Array.isArray(file.mappings) || file.mappings.length === 0) {
    throw new Error("Het koppelbestand is leeg of heeft een onbekende versie");
  }
  if (file.mappings.length > 5000) {
    throw new Error("Het koppelbestand bevat te veel regels");
  }
  const mappings = file.mappings.map((mapping) => {
    if (
      !mapping ||
      typeof mapping.sku_code !== "string" ||
      typeof mapping.source_product_id !== "string" ||
      !mapping.sku_code.trim() ||
      !mapping.source_product_id.trim()
    ) {
      throw new Error("Een regel mist een SKU-code of adviesproduct-ID");
    }
    return {
      sku_code: mapping.sku_code.trim(),
      source_product_id: mapping.source_product_id.trim(),
    };
  });
  return { version: 1, mappings, source_report: file.source_report };
}

export function AdviceProductLinkImport({ onApplied }: { onApplied: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<LinkFile | null>(null);
  const [result, setResult] = useState<AdviceProductLinkImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function reset() {
    setFile(null);
    setResult(null);
    setError("");
    if (inputRef.current) inputRef.current.value = "";
  }

  async function readAndPreview(selected: File) {
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const parsed = parseLinkFile(JSON.parse(await selected.text()));
      const preview = await api.importAdviceProductLinks(parsed.mappings, true);
      setFile(parsed);
      setResult(preview);
    } catch (cause) {
      setFile(null);
      setError(cause instanceof Error ? cause.message : "Koppelbestand lezen mislukt");
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!file || !result?.ready) return;
    setBusy(true);
    setError("");
    try {
      const applied = await api.importAdviceProductLinks(file.mappings, false);
      setResult(applied);
      if (applied.ready) {
        toast.success(`${applied.applied} fles-SKU's gekoppeld`);
        onApplied();
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Koppelen mislukt");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        <FileUp className="mr-1.5 h-4 w-4" />
        Advies koppelen
      </Button>
      <Dialog
        open={open}
        onOpenChange={(nextOpen) => {
          setOpen(nextOpen);
          if (!nextOpen) reset();
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Bestaande flessen koppelen</DialogTitle>
            <p className="pr-8 text-sm text-muted-foreground">
              Upload het bestand uit wijnadvies1. Inventory controleert alles
              eerst en maakt geen nieuwe producten aan.
            </p>
          </DialogHeader>
          <DialogBody className="space-y-4">
            <label className="flex cursor-pointer items-center justify-center rounded-lg border border-dashed border-border p-6 text-sm font-medium hover:bg-accent">
              {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <FileUp className="mr-2 h-4 w-4" />}
              {busy ? "Controleren..." : "Kies koppelbestand"}
              <input
                ref={inputRef}
                className="hidden"
                type="file"
                accept="application/json,.json"
                disabled={busy}
                onChange={(event) => {
                  const selected = event.target.files?.[0];
                  if (selected) void readAndPreview(selected);
                }}
              />
            </label>

            {error ? (
              <div className="flex gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                <span>{error}</span>
              </div>
            ) : null}

            {result ? (
              <div className="space-y-3 rounded-lg border border-border p-4">
                <div className="flex items-center gap-2 font-medium">
                  {result.ready ? (
                    <CheckCircle2 className="h-5 w-5 text-green-600" />
                  ) : (
                    <AlertCircle className="h-5 w-5 text-destructive" />
                  )}
                  {result.ready ? "Controle geslaagd" : "Nog niet veilig om toe te passen"}
                </div>
                <dl className="grid grid-cols-2 gap-2 text-sm">
                  <dt>Te koppelen</dt><dd className="text-right font-medium">{result.would_link}</dd>
                  <dt>Al goed gekoppeld</dt><dd className="text-right font-medium">{result.already_linked}</dd>
                  {result.applied > 0 ? <><dt>Zojuist gekoppeld</dt><dd className="text-right font-medium">{result.applied}</dd></> : null}
                </dl>
                {result.issues.length > 0 ? (
                  <ul className="space-y-2 text-sm">
                    {result.issues.map((issue, index) => (
                      <li key={`${issue.code}-${issue.sku_code}-${index}`} className="rounded bg-destructive/10 p-2">
                        <span className="font-medium">{issue.sku_code || issue.source_product_id}: </span>
                        {issue.message}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {file?.source_report && file.source_report.feed_ready === false ? (
                  <p className="rounded bg-yellow-600/10 p-2 text-xs leading-5">
                    Let op: de automatische feed blijft uit. In wijnadvies1 ontbreken
                    nog {file.source_report.missing_product_id_count ?? 0} product-ID&apos;s.
                  </p>
                ) : null}
              </div>
            ) : null}

            {result?.ready && result.dry_run && result.would_link === 0 ? (
              <p className="rounded-lg bg-green-600/10 p-3 text-sm">
                Alle koppelingen uit dit bestand staan al goed.
              </p>
            ) : null}

            {result?.ready && result.dry_run && result.would_link > 0 ? (
              <Button className="w-full" disabled={busy} onClick={() => void apply()}>
                {busy ? "Koppelen..." : `Bevestig ${result.would_link} koppelingen`}
              </Button>
            ) : null}
          </DialogBody>
        </DialogContent>
      </Dialog>
    </>
  );
}
