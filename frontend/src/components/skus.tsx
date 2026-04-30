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
}

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
  const [wineRejected, setWineRejected] = useState<{ file: File; preview: string }[]>([]);
  const [duplicateRejected, setDuplicateRejected] = useState<{ file: File; preview: string; detail: string }[]>([]);

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
      setWineRejected((prev) => {
        prev.forEach((s) => URL.revokeObjectURL(s.preview));
        return [];
      });
      setDuplicateRejected((prev) => {
        prev.forEach((s) => URL.revokeObjectURL(s.preview));
        return [];
      });
    }
  }, [open, sku]);

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
        const hasRejections = await uploadImages(skuId, stagedFiles, false);
        if (hasRejections) return;
      }

      onSaved();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout");
    } finally {
      setSubmitting(false);
    }
  }

  const WINE_REJECTION_MSG = "Dit is geen wijndoos";
  const DUPLICATE_MSG = "Deze foto lijkt te veel op";

  async function uploadImages(
    skuId: number,
    files: { file: File; preview: string }[],
    skipWineCheck: boolean,
    skipDuplicateCheck: boolean = false,
  ): Promise<boolean> {
    setUploading(true);
    const infoToast = toast("Beelden uploaden en verwerken...");
    const results = await Promise.allSettled(
      files.map((staged) => api.uploadImage(skuId, staged.file, skipWineCheck, skipDuplicateCheck)),
    );
    toast.dismiss(infoToast);

    const wineRejects: { file: File; preview: string }[] = [];
    const dupRejects: { file: File; preview: string; detail: string }[] = [];
    const otherErrors: string[] = [];
    let successCount = 0;

    results.forEach((r, i) => {
      if (r.status === "fulfilled") {
        URL.revokeObjectURL(files[i].preview);
        successCount++;
      } else {
        const msg = r.reason instanceof Error ? r.reason.message : "";
        if (msg.includes(DUPLICATE_MSG)) {
          dupRejects.push({ ...files[i], detail: msg });
        } else if (msg.includes(WINE_REJECTION_MSG)) {
          wineRejects.push(files[i]);
        } else {
          URL.revokeObjectURL(files[i].preview);
          otherErrors.push(msg || "Upload mislukt");
        }
      }
    });

    if (successCount > 0) {
      toast.success(`${successCount} referentiebeeld(en) toegevoegd`);
    }
    if (otherErrors.length > 0) {
      toast.error(otherErrors[0]);
    }
    if (wineRejects.length > 0) {
      setWineRejected(wineRejects);
    }
    if (dupRejects.length > 0) {
      setDuplicateRejected(dupRejects);
    }

    setStagedFiles([]);
    setUploading(false);
    loadImages(skuId);
    return wineRejects.length > 0 || dupRejects.length > 0;
  }

  async function forceUploadRejected() {
    if (!currentId || wineRejected.length === 0) return;
    const hasMore = await uploadImages(currentId, wineRejected, true);
    setWineRejected([]);
    if (!hasMore) onSaved();
  }

  function dismissRejected() {
    wineRejected.forEach((s) => URL.revokeObjectURL(s.preview));
    setWineRejected([]);
  }

  async function forceUploadDuplicate() {
    if (!currentId || duplicateRejected.length === 0) return;
    const hasMore = await uploadImages(currentId, duplicateRejected, false, true);
    setDuplicateRejected([]);
    if (!hasMore) onSaved();
  }

  function dismissDuplicate() {
    duplicateRejected.forEach((s) => URL.revokeObjectURL(s.preview));
    setDuplicateRejected([]);
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
                    src={img.image_path.startsWith("/") ? `/api/uploads/${img.image_path.replace(/^\/app\/uploads\//, "")}` : `/api/files/${img.image_path}`}
                    alt="ref"
                    className={`w-full h-full object-cover${img.processing_status !== "done" ? " opacity-50" : ""}`}
                  />
                  {img.processing_status === "pending" || img.processing_status === "processing" ? (
                    <span className="absolute bottom-1 left-1 text-[10px] bg-yellow-600/80 text-white px-1 rounded">
                      Verwerken...
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
              {wineRejected.length > 0 && (
                <div className="mt-3 p-3 rounded-lg border-2 border-yellow-600/50 bg-yellow-600/10">
                  <p className="text-sm font-medium mb-1">
                    Niet herkend als wijndoos
                  </p>
                  <div className="flex gap-2 mb-2">
                    {wineRejected.map((f, i) => (
                      <img
                        key={i}
                        src={f.preview}
                        alt="rejected"
                        className="w-14 h-14 object-cover rounded border border-border"
                      />
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground mb-2">
                    Dit beeld werd niet herkend als een wijndoos. Is het toch een wijndoos?
                  </p>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      type="button"
                      disabled={uploading}
                      onClick={forceUploadRejected}
                    >
                      Toch uploaden
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      type="button"
                      onClick={dismissRejected}
                    >
                      Annuleren
                    </Button>
                  </div>
                </div>
              )}
              {duplicateRejected.length > 0 && (
                <div className="mt-3 p-3 rounded-lg border-2 border-orange-600/50 bg-orange-600/10">
                  <p className="text-sm font-medium mb-1">
                    Mogelijk duplicaat gevonden
                  </p>
                  <div className="flex gap-2 mb-2">
                    {duplicateRejected.map((f, i) => (
                      <img
                        key={i}
                        src={f.preview}
                        alt="duplicate"
                        className="w-14 h-14 object-cover rounded border border-border"
                      />
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground mb-2">
                    {duplicateRejected[0].detail}
                  </p>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      type="button"
                      disabled={uploading}
                      onClick={forceUploadDuplicate}
                    >
                      Toch toevoegen
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      type="button"
                      onClick={dismissDuplicate}
                    >
                      Annuleren
                    </Button>
                  </div>
                </div>
              )}
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
