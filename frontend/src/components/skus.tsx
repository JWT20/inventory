import { useState, useEffect, useCallback, useRef } from "react";
import { toast } from "@/App";
import { adviceSyncConflictMessage, api } from "@/lib/api";
import { useAuth, hasModule } from "@/lib/auth";
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
import { Switch } from "@/components/ui/switch";
import { AlertCircle, Loader2 } from "lucide-react";

interface Supplier {
  id: number;
  name: string;
}

interface Organization {
  id: number;
  name: string;
}

const COURIER_ORG_STORAGE_KEY = "courier.skus.selectedOrgId";

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
  is_bottle: boolean;
  source_product_id: string | null;
  product_type: string;
  ean: string | null;
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

const PAGE_SIZE = 50;

export function SKUsPage() {
  const { user } = useAuth();
  const isCourier = user?.role === "courier";
  const adviceSyncAvailable = Boolean(user?.advice_products_sync_available);
  // EAN orgs scan a barcode into the search box, so advertise it in the hint.
  const canBarcode = hasModule(user, "barcode_picking");
  const [skus, setSkus] = useState<SKU[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [syncingAdvice, setSyncingAdvice] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<SKU | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [courierOrgId, setCourierOrgId] = useState<number | null>(() => {
    if (typeof window === "undefined") return null;
    const stored = window.localStorage.getItem(COURIER_ORG_STORAGE_KEY);
    return stored ? Number(stored) : null;
  });

  // Debounce the search box so each keystroke doesn't hit the server.
  useEffect(() => {
    const t = window.setTimeout(() => setQuery(search.trim()), 300);
    return () => window.clearTimeout(t);
  }, [search]);

  useEffect(() => {
    if (!isCourier) return;
    api
      .listOrganizations()
      .then((list: Organization[]) => setOrgs(list))
      .catch(() => toast.error("Kan organisaties niet laden"));
  }, [isCourier]);

  useEffect(() => {
    if (!isCourier) return;
    if (courierOrgId === null) {
      window.localStorage.removeItem(COURIER_ORG_STORAGE_KEY);
    } else {
      window.localStorage.setItem(COURIER_ORG_STORAGE_KEY, String(courierOrgId));
    }
  }, [isCourier, courierOrgId]);

  // Monotonic request token: only the newest in-flight request may write
  // state, so a slow response for a stale search/org can't overwrite or
  // append to fresher results.
  const reqIdRef = useRef(0);

  // Reload the first page whenever the org or the (debounced) search changes.
  const load = useCallback(async () => {
    const reqId = ++reqIdRef.current;
    if (isCourier && courierOrgId === null) {
      setSkus([]);
      setHasMore(false);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const page = await api.listSKUs(
        false,
        isCourier ? courierOrgId ?? undefined : undefined,
        { search: query, limit: PAGE_SIZE, offset: 0 },
      );
      if (reqId !== reqIdRef.current) return;
      setSkus(page);
      setHasMore(page.length === PAGE_SIZE);
    } catch {
      if (reqId === reqIdRef.current) toast.error("Kan SKU's niet laden");
    } finally {
      if (reqId === reqIdRef.current) setLoading(false);
    }
  }, [isCourier, courierOrgId, query]);

  useEffect(() => {
    load();
  }, [load]);

  // Pull the advice app's bottle products in now instead of waiting for the
  // periodic sync, so a wine created there is linkable here right away.
  const syncAdviceProducts = useCallback(async () => {
    setSyncingAdvice(true);
    try {
      const summary = await api.syncAdviceProducts();
      await load();
      const conflictMessage = adviceSyncConflictMessage(summary);
      if (conflictMessage) {
        toast.error(
          `${summary.created} nieuw, ${summary.updated} bijgewerkt; `
          + conflictMessage,
        );
      } else {
        toast.success(`${summary.created} nieuw, ${summary.updated} bijgewerkt`);
      }
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Synchroniseren mislukt");
    } finally {
      setSyncingAdvice(false);
    }
  }, [load]);

  const loadMore = useCallback(async () => {
    const reqId = ++reqIdRef.current;
    setLoadingMore(true);
    try {
      const page = await api.listSKUs(
        false,
        isCourier ? courierOrgId ?? undefined : undefined,
        { search: query, limit: PAGE_SIZE, offset: skus.length },
      );
      if (reqId !== reqIdRef.current) return;
      setSkus((prev) => [...prev, ...page]);
      setHasMore(page.length === PAGE_SIZE);
    } catch {
      if (reqId === reqIdRef.current) toast.error("Kan meer producten niet laden");
    } finally {
      if (reqId === reqIdRef.current) setLoadingMore(false);
    }
  }, [isCourier, courierOrgId, query, skus.length]);

  const filteredSkus = skus;

  const courierNeedsOrg = isCourier && courierOrgId === null;

  return (
    <>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Producten</h2>
        {user && user.role !== "courier" && (
          <div className="flex items-center gap-2">
            {adviceSyncAvailable && (
              <Button
                size="sm"
                variant="outline"
                disabled={syncingAdvice}
                onClick={() => {
                  void syncAdviceProducts();
                }}
              >
                {syncingAdvice ? "Bezig…" : "Synchroniseer nu"}
              </Button>
            )}
            <Button size="sm" onClick={() => setShowNew(true)}>
              + Nieuw
            </Button>
          </div>
        )}
      </div>

      {isCourier && (
        <div className="mb-4 space-y-1">
          <Label className="text-xs">Organisatie</Label>
          <Select
            value={courierOrgId ? String(courierOrgId) : ""}
            onValueChange={(v) => setCourierOrgId(v ? Number(v) : null)}
          >
            <SelectTrigger>
              <SelectValue placeholder="Kies een organisatie" />
            </SelectTrigger>
            <SelectContent>
              {orgs.map((o) => (
                <SelectItem key={o.id} value={String(o.id)}>
                  {o.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {!courierNeedsOrg && (
        <Input
          placeholder={
            canBarcode
              ? "Zoek op naam, code, EAN, producent..."
              : "Zoek op naam, code, producent..."
          }
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="mb-4"
        />
      )}

      <div className="space-y-3">
        {courierNeedsOrg ? (
          <p className="text-center text-muted-foreground py-10">
            Kies eerst een organisatie om producten te zien.
          </p>
        ) : loading ? (
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
              {s.product_type === "barcode" && s.ean && (
                <p className="text-sm text-muted-foreground mt-1">
                  EAN {s.ean}
                </p>
              )}
              {s.product_type !== "barcode" && (
                <p className="text-sm text-muted-foreground mt-1">
                  {s.image_count} referentiebeeld
                  {s.image_count !== 1 ? "en" : ""}
                </p>
              )}
            </Card>
          ))
        )}
      </div>

      {!courierNeedsOrg && !loading && hasMore && (
        <div className="mt-4 flex justify-center">
          <Button
            variant="secondary"
            size="sm"
            onClick={loadMore}
            disabled={loadingMore}
          >
            {loadingMore ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Laden...
              </>
            ) : (
              "Meer tonen"
            )}
          </Button>
        </div>
      )}

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
  const isCourier = user?.role === "courier";
  const adviceSyncAvailable = Boolean(user?.advice_products_sync_available);
  // The product form may only offer pick methods the org has a module for; the
  // invariant on the backend rejects anything else. Default a new product to
  // whichever the org supports (vision wins when both are enabled).
  const canVision = hasModule(user, "vision_picking");
  const canBarcode = hasModule(user, "barcode_picking");
  const canSuppliers = hasModule(user, "suppliers");
  const defaultProductType: "vision" | "barcode" = canVision ? "vision" : "barcode";
  const canDeleteProduct =
    !!user &&
    (user.is_platform_admin || user.role === "owner" || user.role === "member");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [producent, setProducent] = useState("");
  const [wijnaam, setWijnaam] = useState("");
  const [wijntype, setWijntype] = useState("");
  const [volume, setVolume] = useState("");
  const [supplierId, setSupplierId] = useState<number | null>(null);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [isBottle, setIsBottle] = useState(false);
  const [sourceProductId, setSourceProductId] = useState("");
  // "vision" = wine (photo + AI); "barcode" = identified by its EAN scan.
  const [productType, setProductType] = useState<"vision" | "barcode">("vision");
  const [naam, setNaam] = useState("");
  const [skuCode, setSkuCode] = useState("");
  const [ean, setEan] = useState("");

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
    // Only fetch suppliers when the org has the module; couriers (no org) and
    // barcode-only orgs would otherwise trigger a spurious 403 on /suppliers.
    if (open && canSuppliers) {
      api.listSuppliers().then(setSuppliers).catch(() => {});
    }
    if (open && sku) {
      const a = sku.attributes || {};
      setProducent(a.producent || "");
      setWijnaam(a.wijnaam || "");
      setWijntype(a.wijntype || "");
      setVolume(a.volume || "");
      setSupplierId(sku.supplier_id ?? null);
      setIsBottle(sku.is_bottle ?? false);
      setSourceProductId(sku.source_product_id ?? "");
      setProductType(sku.product_type === "barcode" ? "barcode" : "vision");
      setNaam(sku.name ?? "");
      setSkuCode(sku.sku_code ?? "");
      setEan(sku.ean ?? "");
      setCurrentId(sku.id);
      loadImages(sku.id);
    } else if (open) {
      setProducent("");
      setWijnaam("");
      setWijntype("");
      setVolume("");
      setSupplierId(null);
      setIsBottle(false);
      setSourceProductId("");
      setProductType(defaultProductType);
      setNaam("");
      setSkuCode("");
      setEan("");
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
      if (productType === "barcode") {
        // Barcode product (e.g. socks): identified by its EAN, no photos.
        const payload = {
          name: naam.trim(),
          sku_code: skuCode.trim(),
          product_type: "barcode" as const,
          ean: ean.trim(),
          is_bottle: isBottle,
        };
        if (skuId) {
          await api.updateSKU(skuId, payload);
          toast.success("Product bijgewerkt");
        } else {
          const created = await api.createSKU({ category: "overig", attributes: {}, ...payload });
          skuId = created.id;
          setCurrentId(skuId);
          toast.success("Product aangemaakt");
        }
      } else if (skuId) {
        await api.updateSKU(skuId, {
          attributes: getAttributes(),
          supplier_id: supplierId,
          is_bottle: isBottle,
          source_product_id: sourceProductId.trim() || null,
        });
        toast.success("SKU bijgewerkt");
      } else {
        const created = await api.createSKU({
          category: "wine",
          attributes: getAttributes(),
          supplier_id: supplierId,
          is_bottle: isBottle,
          source_product_id: sourceProductId.trim() || null,
        });
        skuId = created.id;
        setCurrentId(skuId);
        toast.success("SKU aangemaakt");
      }

      if (skuId && productType === "vision" && stagedFiles.length > 0) {
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
    e.target.value = "";
    if (isCourier && currentId) {
      // Couriers have no Save button — upload immediately.
      uploadImages(currentId, [{ file, preview }], false);
      return;
    }
    setStagedFiles((prev) => [...prev, { file, preview }]);
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
    if (img.processing_error_code === WINE_CHECK_FAILED) return "Niet herkend als wijndoos of fles";
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
          <div className="space-y-1">
            <Label className="text-xs">Soort product</Label>
            <Select
              value={productType}
              onValueChange={(v) => setProductType(v as "vision" | "barcode")}
              disabled={isCourier || currentId !== null}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {canVision && <SelectItem value="vision">Wijn (foto)</SelectItem>}
                {canBarcode && <SelectItem value="barcode">Barcode (EAN)</SelectItem>}
              </SelectContent>
            </Select>
          </div>

          {productType === "barcode" ? (
            <>
              <div className="space-y-1">
                <Label className="text-xs">Naam</Label>
                <Input
                  value={naam}
                  onChange={(e) => setNaam(e.target.value)}
                  placeholder="Wielersok zwart M"
                  required
                  disabled={isCourier}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">SKU-code</Label>
                <Input
                  value={skuCode}
                  onChange={(e) => setSkuCode(e.target.value)}
                  placeholder="SOK-ZW-M"
                  required
                  disabled={isCourier}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">EAN (streepjescode)</Label>
                <Input
                  value={ean}
                  onChange={(e) => setEan(e.target.value)}
                  placeholder="8712345678906"
                  inputMode="numeric"
                  required
                  disabled={isCourier}
                />
              </div>
            </>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label className="text-xs">Producent</Label>
                  <Input
                    value={producent}
                    onChange={(e) => setProducent(e.target.value)}
                    placeholder="Château Margaux"
                    required
                    disabled={isCourier}
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Wijnaam</Label>
                  <Input
                    value={wijnaam}
                    onChange={(e) => setWijnaam(e.target.value)}
                    placeholder="Grand Vin"
                    required
                    disabled={isCourier}
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
                    disabled={isCourier}
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Volume</Label>
                  <Input
                    value={volume}
                    onChange={(e) => setVolume(e.target.value)}
                    placeholder="750"
                    required
                    disabled={isCourier}
                  />
                </div>
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Leverancier</Label>
                <Select
                  value={supplierId ? String(supplierId) : "none"}
                  onValueChange={(v) => setSupplierId(v === "none" ? null : Number(v))}
                  disabled={isCourier}
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
              <div className="flex items-center gap-2">
                <Switch
                  checked={isBottle}
                  onCheckedChange={setIsBottle}
                  disabled={isCourier || Boolean(sourceProductId)}
                />
                <Label className="text-xs">Dit product is een losse fles</Label>
              </div>
              {isBottle && (
                <div className="space-y-1">
                  <Label className="text-xs">Adviesproduct-ID</Label>
                  <Input
                    value={sourceProductId}
                    onChange={(e) => setSourceProductId(e.target.value)}
                    placeholder="prd_01H8..."
                    required={adviceSyncAvailable}
                    disabled={isCourier}
                  />
                  <p className="text-xs text-muted-foreground">
                    {adviceSyncAvailable
                      ? "Verplicht voor flessen van deze organisatie. Gebruik bij voorkeur Synchroniseer nu."
                      : "Stabiele koppeling met de advies-app; leeg laten voor niet-gekoppelde flessen."}
                  </p>
                </div>
              )}
            </>
          )}
          {!isCourier && (
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting
                ? currentId
                  ? "Opslaan..."
                  : "Aanmaken..."
                : "Opslaan"}
            </Button>
          )}
        </form>

        {productType === "vision" && (
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
                  <button
                    onClick={() => deleteImage(img.id)}
                    className="absolute top-1 right-1 bg-red-600/80 text-white rounded-full w-5 h-5 text-xs flex items-center justify-center"
                  >
                    &times;
                  </button>
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
                  </div>
                </div>
              ))}
            </div>
          )}
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
        </div>
        )}

        {canDeleteProduct && currentId && (
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
