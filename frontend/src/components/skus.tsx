import { useState, useEffect, useCallback, useRef } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { AlertCircle, Loader2 } from "lucide-react";

interface Supplier {
  id: number;
  name: string;
}

interface SKU {
  id: number;
  sku_code: string;
  name: string;
  description: string | null;
  active: boolean;
  category: string | null;
  attributes: Record<string, string>;
  supplier_id: number | null;
  supplier_name: string | null;
  image_count: number;
}

interface RefImage {
  id: number;
  sku_id: number;
  image_path: string;
  vision_description: string | null;
  processing_status: string;
  processing_error_code: string | null;
  processing_error_message: string | null;
  duplicate_sku_id: number | null;
}

const WINE_CHECK_FAILED = "wine_check_failed";
const DUPLICATE_CHECK_FAILED = "duplicate_check_failed";

function SKUCardSkeleton() {
  return (
    <Card className="p-4">
      <div className="flex justify-between items-center mb-1">
        <Skeleton className="h-5 w-44" />
        <Skeleton className="h-5 w-14 rounded-full" />
      </div>
      <Skeleton className="h-4 w-28 mt-1" />
      <Skeleton className="h-4 w-36 mt-1" />
    </Card>
  );
}

export function SKUsPage() {
  const { user } = useAuth();
  const [skus, setSkus] = useState<SKU[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<SKU | null>(null);
  const [showNew, setShowNew] = useState(false);

  const load = useCallback(async () => {
    try {
      setSkus(await api.listSKUs());
    } catch {
      toast.error("Kan SKU's niet laden");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const filteredSkus = skus.filter((s) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      s.name.toLowerCase().includes(q) ||
      s.sku_code.toLowerCase().includes(q) ||
      (s.attributes?.producent || "").toLowerCase().includes(q) ||
      (s.category || "").toLowerCase().includes(q)
    );
  });

  return (
    <>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Producten</h2>
        {user && user.role !== "courier" && (
          <Button size="sm" onClick={() => setShowNew(true)}>
            + Nieuw
          </Button>
        )}
      </div>

      <Input
        placeholder="Zoek op naam, code, producent..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="mb-4"
      />

      <div className="space-y-3">
        {loading ? (
          Array.from({ length: 3 }).map((_, i) => <SKUCardSkeleton key={i} />)
        ) : filteredSkus.length === 0 ? (
          <p className="text-center text-muted-foreground py-10">
            Geen SKU's gevonden
          </p>
        ) : (
          filteredSkus.map((s) => (
            <Card
              key={s.id}
              className="p-4 cursor-pointer active:scale-[0.98] transition-transform"
              onClick={() => setEditing(s)}
            >
              <div className="flex justify-between items-center mb-1">
                <span className="font-semibold">{s.name}</span>
                <Badge variant={s.active ? "active" : "inactive"}>
                  {s.active ? "Actief" : "Inactief"}
                </Badge>
              </div>
              <p className="text-sm text-muted-foreground">{s.sku_code}</p>
              <p className="text-sm text-muted-foreground mt-1">
                {s.image_count} referentiebeeld
                {s.image_count !== 1 ? "en" : ""}
              </p>
            </Card>
          ))
        )}
      </div>

      <SKUDialog
        open={showNew}
        onClose={() => setShowNew(false)}
        onSaved={load}
      />
      <SKUDialog
        open={!!editing}
        sku={editing ?? undefined}
        onClose={() => setEditing(null)}
        onSaved={load}
      />
    </>
  );
}

function SKUDialog({
  open,
  sku,
  onClose,
  onSaved,
}: {
  open: boolean;
  sku?: SKU;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { user } = useAuth();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [producent, setProducent] = useState("");
  const [wijnaam, setWijnaam] = useState("");
  const [wijntype, setWijntype] = useState("");
  const [volume, setVolume] = useState("");
  const [supplierId, setSupplierId] = useState<number | null>(null);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);

  // Helper to build attributes dict from individual state fields
  const getAttributes = () => ({
    producent,
    wijnaam,
    wijntype,
    volume,
  });
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [images, setImages] = useState<RefImage[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [stagedFiles, setStagedFiles] = useState<{ file: File; preview: string }[]>([]);
  const hasProcessingImages = images.some(
    (img) => img.processing_status === "pending" || img.processing_status === "processing",
  );
  const failedImages = images.filter((img) => img.processing_status === "failed");

  useEffect(() => {
    if (open) {
      api.listSuppliers().then(setSuppliers).catch(() => {});
    }
    if (open && sku) {
      const a = sku.attributes || {};
      setProducent(a.producent || "");
      setWijnaam(a.wijnaam || "");
      setWijntype(a.wijntype || "");
      setVolume(a.volume || "");
      setSupplierId(sku.supplier_id ?? null);
      setCurrentId(sku.id);
      loadImages(sku.id);
    } else if (open) {
      setProducent("");
      setWijnaam("");
      setWijntype("");
      setVolume("");
      setSupplierId(null);
      setCurrentId(null);
      setImages([]);
    }
    if (!open) {
      setStagedFiles((prev) => {
        prev.forEach((s) => URL.revokeObjectURL(s.preview));
        return [];
      });
    }
  }, [open, sku]);

  useEffect(() => {
    if (!open || !currentId || !hasProcessingImages) return;
    const skuId = currentId;
    const interval = window.setInterval(async () => {
      try {
        const statuses: { id: number; processing_status: string }[] =
          await api.listImageStatuses(skuId);
        const byId = new Map(statuses.map((s) => [s.id, s.processing_status]));
        let needsRefetch = false;
        setImages((prev) => {
          const next = prev.map((img) => {
            const newStatus = byId.get(img.id);
            if (!newStatus || newStatus === img.processing_status) return img;
            if (newStatus !== "pending" && newStatus !== "processing") {
              needsRefetch = true;
            }
            return { ...img, processing_status: newStatus };
          });
          return next;
        });
        if (needsRefetch) loadImages(skuId);
      } catch {
        /* ignore — next tick will retry */
      }
    }, 2500);
    return () => window.clearInterval(interval);
  }, [open, currentId, hasProcessingImages]);

  async function loadImages(id: number) {
    try {
      setImages(await api.listImages(id));
    } catch {
      /* ignore */
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!user || user.role === "courier") return;
    setSubmitting(true);
    try {
      let skuId = currentId;
      if (skuId) {
        await api.updateSKU(skuId, {
          attributes: getAttributes(),
          supplier_id: supplierId,
        });
        toast.success("SKU bijgewerkt");
      } else {
        const created = await api.createSKU({
          category: "wine",
          attributes: getAttributes(),
          supplier_id: supplierId,
        });
        skuId = created.id;
        setCurrentId(skuId);
        toast.success("SKU aangemaakt");
      }

      if (stagedFiles.length > 0) {
        await uploadImages(skuId, stagedFiles, false);
      }

      onSaved();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout");
    } finally {
      setSubmitting(false);
    }
  }

  async function uploadImages(
    skuId: number,
    files: { file: File; preview: string }[],
    skipWineCheck: boolean,
    skipDuplicateCheck: boolean = false,
  ) {
    setUploading(true);
    const infoToast = toast("Beelden opslaan...");
    const results = await Promise.allSettled(
      files.map((staged) => api.uploadImage(skuId, staged.file, skipWineCheck, skipDuplicateCheck)),
    );
    toast.dismiss(infoToast);

    const otherErrors: string[] = [];
    let successCount = 0;

    results.forEach((r, i) => {
      if (r.status === "fulfilled") {
        URL.revokeObjectURL(files[i].preview);
        successCount++;
      } else {
        const msg = r.reason instanceof Error ? r.reason.message : "";
        URL.revokeObjectURL(files[i].preview);
        otherErrors.push(msg || "Upload mislukt");
      }
    });

    if (successCount > 0) {
      toast.success(
        `${successCount} beeld${successCount !== 1 ? "en" : ""} opgeslagen. Analyse loopt op achtergrond; je kunt doorgaan.`,
      );
    }
    if (otherErrors.length > 0) {
      toast.error(otherErrors[0]);
    }

    setStagedFiles([]);
    setUploading(false);
    loadImages(skuId);
  }

  async function retryImageProcessing(
    image: RefImage,
    skipWineCheck: boolean,
    skipDuplicateCheck: boolean,
  ) {
    if (!currentId) return;
    setUploading(true);
    try {
      await api.retryImageProcessing(currentId, image.id, skipWineCheck, skipDuplicateCheck);
      toast.success("Analyse opnieuw gestart. Je kunt doorgaan.");
      loadImages(currentId);
      onSaved();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Kan analyse niet opnieuw starten");
    } finally {
      setUploading(false);
    }
  }

  function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const preview = URL.createObjectURL(file);
    setStagedFiles((prev) => [...prev, { file, preview }]);
    e.target.value = "";
  }

  function removeStagedFile(index: number) {
    setStagedFiles((prev) => {
      URL.revokeObjectURL(prev[index].preview);
      return prev.filter((_, i) => i !== index);
    });
  }

  async function handleDeleteSKU() {
    if (!currentId || !sku) return;
    if (!confirm(`Product "${sku.name}" verwijderen? Dit verwijdert ook alle referentiebeelden.`)) return;
    setDeleting(true);
    try {
      await api.deleteSKU(currentId);
      toast.success("Product verwijderd");
      onSaved();
      onClose();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Verwijderen mislukt";
      if (msg.includes("still referenced by")) {
        const force = confirm(
          `${msg}\n\nWil je het product inclusief alle gekoppelde gegevens definitief verwijderen?`
        );
        if (force) {
          try {
            await api.deleteSKU(currentId, true);
            toast.success("Product verwijderd");
            onSaved();
            onClose();
          } catch (err2: unknown) {
            toast.error(err2 instanceof Error ? err2.message : "Verwijderen mislukt");
          }
        }
      } else {
        toast.error(msg);
      }
    } finally {
      setDeleting(false);
    }
  }

  async function deleteImage(imageId: number) {
    if (!currentId) return;
    try {
      await api.deleteImage(currentId, imageId);
      loadImages(currentId);
      onSaved();
      toast.success("Beeld verwijderd");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Kan beeld niet verwijderen");
    }
  }

  function imageSrc(img: RefImage) {
    const path = img.image_path;
    if (path.startsWith("/app/uploads/")) {
      return `/api/uploads/${path.slice("/app/uploads/".length)}`;
    }
    if (path.startsWith("/")) {
      return `/api/uploads/${path.replace(/^\/+/, "")}`;
    }
    return `/api/files/${path}`;
  }

  function failedImageTitle(img: RefImage) {
    if (img.processing_error_code === WINE_CHECK_FAILED) return "Niet herkend als wijndoos";
    if (img.processing_error_code === DUPLICATE_CHECK_FAILED) return "Mogelijk duplicaat gevonden";
    return "Beeldanalyse mislukt";
  }

  return (
    <Sheet open={open} onOpenChange={(v) => !v && onClose()}>
      <SheetContent side="bottom">
        <SheetHeader>
          <SheetTitle>
            {sku ? "Product bewerken" : "Nieuw product"}
            {sku && (
              <span className="ml-2 text-sm font-normal text-muted-foreground">
                {sku.sku_code}
              </span>
            )}
          </SheetTitle>
        </SheetHeader>

        <form onSubmit={submit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-xs">Producent</Label>
              <Input
                value={producent}
                onChange={(e) => setProducent(e.target.value)}
                placeholder="Château Margaux"
                required
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Wijnaam</Label>
              <Input
                value={wijnaam}
                onChange={(e) => setWijnaam(e.target.value)}
                placeholder="Grand Vin"
                required
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-xs">Type</Label>
              <Input
                value={wijntype}
                onChange={(e) => setWijntype(e.target.value)}
                placeholder="Rood"
                required
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Volume</Label>
              <Input
                value={volume}
                onChange={(e) => setVolume(e.target.value)}
                placeholder="750"
                required
              />
            </div>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Leverancier</Label>
            <Select
              value={supplierId ? String(supplierId) : "none"}
              onValueChange={(v) => setSupplierId(v === "none" ? null : Number(v))}
            >
              <SelectTrigger>
                <SelectValue placeholder="Geen leverancier" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Geen leverancier</SelectItem>
                {suppliers.map((s) => (
                  <SelectItem key={s.id} value={String(s.id)}>{s.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {user && user.role !== "courier" && (
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting
                ? currentId
                  ? "Opslaan..."
                  : "Aanmaken..."
                : "Opslaan"}
            </Button>
          )}
        </form>

        <div className="mt-6">
          <Label className="mb-2 block">Referentiebeelden</Label>
          {stagedFiles.length > 0 && (
            <p className="text-xs text-muted-foreground mb-2">
              Nieuwe beelden worden opgeslagen zodra je op Opslaan drukt.
            </p>
          )}
          {hasProcessingImages && (
            <div className="mb-3 flex items-center gap-2 rounded-md border border-yellow-600/30 bg-yellow-600/10 px-3 py-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-yellow-700" />
              <span>Analyse loopt op achtergrond. Je kunt doorgaan.</span>
            </div>
          )}
          {images.length === 0 && stagedFiles.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nog geen referentiebeelden
            </p>
          ) : (
            <div className="grid grid-cols-4 gap-2 mb-3">
              {images.map((img) => (
                <div
                  key={img.id}
                  className="relative aspect-square rounded-lg overflow-hidden border border-border"
                >
                  <img
                    src={imageSrc(img)}
                    alt="ref"
                    className={`w-full h-full object-cover${img.processing_status !== "done" ? " opacity-50" : ""}`}
                  />
                  {img.processing_status === "pending" || img.processing_status === "processing" ? (
                    <span className="absolute bottom-1 left-1 flex items-center gap-1 text-[10px] bg-yellow-600/85 text-white px-1 rounded">
                      <Loader2 className="h-2.5 w-2.5 animate-spin" />
                      Analyseren
                    </span>
                  ) : img.processing_status === "failed" ? (
                    <span className="absolute bottom-1 left-1 text-[10px] bg-red-600/80 text-white px-1 rounded">
                      Mislukt
                    </span>
                  ) : null}
                  {user && user.role !== "courier" && (
                    <button
                      onClick={() => deleteImage(img.id)}
                      className="absolute top-1 right-1 bg-red-600/80 text-white rounded-full w-5 h-5 text-xs flex items-center justify-center"
                    >
                      &times;
                    </button>
                  )}
                </div>
              ))}
              {stagedFiles.map((staged, i) => (
                <div
                  key={`staged-${i}`}
                  className="relative aspect-square rounded-lg overflow-hidden border-2 border-dashed border-primary/50"
                >
                  <img
                    src={staged.preview}
                    alt="preview"
                    className="w-full h-full object-cover opacity-80"
                  />
                  <span className="absolute bottom-1 left-1 text-[10px] bg-primary/80 text-primary-foreground px-1 rounded">
                    Na opslaan
                  </span>
                  <button
                    onClick={() => removeStagedFile(i)}
                    className="absolute top-1 right-1 bg-red-600/80 text-white rounded-full w-5 h-5 text-xs flex items-center justify-center"
                  >
                    &times;
                  </button>
                </div>
              ))}
            </div>
          )}
          {failedImages.length > 0 && (
            <div className="space-y-2 mb-3">
              {failedImages.map((img) => (
                <div
                  key={`failed-${img.id}`}
                  className="flex gap-3 rounded-md border border-red-600/30 bg-red-600/10 p-3"
                >
                  <img
                    src={imageSrc(img)}
                    alt=""
                    className="h-14 w-14 flex-none rounded border border-border object-cover opacity-80"
                  />
                  <div className="min-w-0 flex-1">
                    <p className="flex items-center gap-1.5 text-sm font-medium">
                      <AlertCircle className="h-4 w-4 text-red-700" />
                      {failedImageTitle(img)}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {img.processing_error_message || "De beeldanalyse is mislukt."}
                    </p>
                    {user && user.role !== "courier" && (
                      <div className="mt-2 flex flex-wrap gap-2">
                        {img.processing_error_code === WINE_CHECK_FAILED && (
                          <Button
                            size="sm"
                            variant="outline"
                            type="button"
                            disabled={uploading}
                            onClick={() => retryImageProcessing(img, true, false)}
                          >
                            Toch uploaden
                          </Button>
                        )}
                        {img.processing_error_code === DUPLICATE_CHECK_FAILED && (
                          <Button
                            size="sm"
                            variant="outline"
                            type="button"
                            disabled={uploading}
                            onClick={() => retryImageProcessing(img, false, true)}
                          >
                            Toch toevoegen
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          type="button"
                          disabled={uploading}
                          onClick={() => deleteImage(img.id)}
                        >
                          Verwijderen
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
          {user && user.role !== "courier" && (
            <>
              <Button
                variant="secondary"
                size="sm"
                type="button"
                disabled={uploading}
                onClick={() => fileInputRef.current?.click()}
              >
                {uploading ? "Uploaden..." : "Foto toevoegen"}
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                capture="environment"
                className="hidden"
                onChange={handleUpload}
              />
            </>
          )}
        </div>

        {user?.is_platform_admin && currentId && (
          <div className="mt-6 pt-4 border-t border-border">
            <Button
              variant="destructive"
              className="w-full"
              onClick={handleDeleteSKU}
              disabled={deleting}
            >
              {deleting ? "Verwijderen..." : "Product verwijderen"}
            </Button>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
