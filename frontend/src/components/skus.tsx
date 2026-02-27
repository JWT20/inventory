import { useState, useEffect, useCallback, useRef } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
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
        <h2 className="text-xl font-bold">SKU Beheer</h2>
        {user?.is_admin && (
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
  const [skuCode, setSkuCode] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [images, setImages] = useState<RefImage[]>([]);

  useEffect(() => {
    if (open && sku) {
      setSkuCode(sku.sku_code);
      setName(sku.name);
      setDescription(sku.description || "");
      setCurrentId(sku.id);
      loadImages(sku.id);
    } else if (open) {
      setSkuCode("");
      setName("");
      setDescription("");
      setCurrentId(null);
      setImages([]);
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
    if (!user?.is_admin) return;
    try {
      if (currentId) {
        await api.updateSKU(currentId, {
          name,
          description: description || null,
        });
        toast.success("SKU bijgewerkt");
      } else {
        const created = await api.createSKU({
          sku_code: skuCode,
          name,
          description: description || undefined,
        });
        setCurrentId(created.id);
        toast.success("SKU aangemaakt — voeg nu referentiebeelden toe");
      }
      onSaved();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout");
    }
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !currentId) return;
    toast("Beeld uploaden en verwerken...");
    try {
      await api.uploadImage(currentId, file);
      loadImages(currentId);
      onSaved();
      toast.success("Referentiebeeld toegevoegd");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Uploadfout");
    }
    e.target.value = "";
  }

  async function deleteImage(imageId: number) {
    if (!currentId) return;
    await api.deleteImage(currentId, imageId);
    loadImages(currentId);
    onSaved();
    toast.success("Beeld verwijderd");
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{sku ? "SKU Bewerken" : "Nieuwe SKU"}</DialogTitle>
        </DialogHeader>

        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label>SKU Code</Label>
            <Input
              value={skuCode}
              onChange={(e) => setSkuCode(e.target.value)}
              disabled={!!currentId}
              required
            />
          </div>
          <div className="space-y-2">
            <Label>Naam</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className="space-y-2">
            <Label>Omschrijving</Label>
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
            />
          </div>
          {user?.is_admin && (
            <Button type="submit" className="w-full">
              Opslaan
            </Button>
          )}
        </form>

        {currentId && (
          <div className="mt-6">
            <Label className="mb-2 block">Referentiebeelden</Label>
            {images.length === 0 ? (
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
                    {user?.is_admin && (
                      <button
                        onClick={() => deleteImage(img.id)}
                        className="absolute top-1 right-1 bg-red-600/80 text-white rounded-full w-5 h-5 text-xs flex items-center justify-center"
                      >
                        &times;
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
            {user?.is_admin && (
              <>
                <Button
                  variant="secondary"
                  size="sm"
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                >
                  Foto uploaden
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
        )}
      </DialogContent>
    </Dialog>
  );
}
