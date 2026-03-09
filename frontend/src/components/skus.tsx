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
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface SKU {
  id: number;
  sku_code: string;
  name: string;
  description: string | null;
  active: boolean;
  producent: string | null;
  wijnaam: string | null;
  wijntype: string | null;
  jaargang: string | null;
  volume: string | null;
  image_count: number;
}

interface RefImage {
  id: number;
  sku_id: number;
  image_path: string;
  vision_description: string | null;
}

export function SKUsPage() {
  const { user } = useAuth();
  const [skus, setSkus] = useState<SKU[]>([]);
  const [editing, setEditing] = useState<SKU | null>(null);
  const [showNew, setShowNew] = useState(false);

  const load = useCallback(async () => {
    try {
      setSkus(await api.listSKUs());
    } catch {
      toast.error("Kan SKU's niet laden");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

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

      <div className="space-y-3">
        {skus.length === 0 ? (
          <p className="text-center text-muted-foreground py-10">
            Geen SKU's gevonden
          </p>
        ) : (
          skus.map((s) => (
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
  const [jaargang, setJaargang] = useState("");
  const [volume, setVolume] = useState("");
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [images, setImages] = useState<RefImage[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [stagedFiles, setStagedFiles] = useState<{ file: File; preview: string }[]>([]);

  useEffect(() => {
    if (open && sku) {
      setProducent(sku.producent || "");
      setWijnaam(sku.wijnaam || "");
      setWijntype(sku.wijntype || "");
      setJaargang(sku.jaargang || "");
      setVolume(sku.volume || "");
      setCurrentId(sku.id);
      loadImages(sku.id);
    } else if (open) {
      setProducent("");
      setWijnaam("");
      setWijntype("");
      setJaargang("");
      setVolume("");
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
          producent,
          wijnaam,
          wijntype,
          jaargang,
          volume,
        });
        toast.success("SKU bijgewerkt");
      } else {
        const created = await api.createSKU({
          producent,
          wijnaam,
          wijntype,
          jaargang,
          volume,
        });
        skuId = created.id;
        setCurrentId(skuId);
        toast.success("SKU aangemaakt");
      }

      if (stagedFiles.length > 0) {
        setUploading(true);
        const infoToast = toast("Beelden uploaden en verwerken...");
        let uploadErrors = 0;
        for (const staged of stagedFiles) {
          try {
            await api.uploadImage(skuId, staged.file);
          } catch {
            uploadErrors++;
          }
        }
        toast.dismiss(infoToast);
        if (uploadErrors > 0) {
          toast.error(`${uploadErrors} beeld(en) niet geüpload`);
        } else {
          toast.success(`${stagedFiles.length} referentiebeeld(en) toegevoegd`);
        }
        stagedFiles.forEach((s) => URL.revokeObjectURL(s.preview));
        setStagedFiles([]);
        setUploading(false);
        loadImages(skuId);
      }

      onSaved();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout");
    } finally {
      setSubmitting(false);
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
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {sku ? "Product bewerken" : "Nieuw product"}
            {sku && (
              <span className="ml-2 text-sm font-normal text-muted-foreground">
                {sku.sku_code}
              </span>
            )}
          </DialogTitle>
        </DialogHeader>

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
          <div className="grid grid-cols-3 gap-3">
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
              <Label className="text-xs">Jaargang</Label>
              <Input
                value={jaargang}
                onChange={(e) => setJaargang(e.target.value)}
                placeholder="2019"
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
                    src={`/api/uploads/reference_images/${img.sku_id}/${img.image_path.split("/").pop()}`}
                    alt="ref"
                    className="w-full h-full object-cover"
                  />
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
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
