import { useState, useEffect, useCallback } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Trash2, X, MapPin } from "lucide-react";

interface LocationSKU {
  sku_id: number;
  sku_code: string;
  name: string;
  ean: string | null;
  organization_name: string | null;
  is_primary: boolean;
}

interface Location {
  id: number;
  code: string;
  rij: string | null;
  kast: string | null;
  plank: string | null;
  active: boolean;
  created_at: string;
  skus: LocationSKU[];
}

interface AvailableSKU {
  id: number;
  sku_code: string;
  name: string;
  ean: string | null;
  organization_name: string | null;
}

function locationSubtitle(l: Location): string {
  const parts = [
    l.rij && `rij ${l.rij}`,
    l.kast && `kast ${l.kast}`,
    l.plank && `plank ${l.plank}`,
  ].filter(Boolean);
  return parts.join(" · ");
}

export function LocationsPage() {
  const [locations, setLocations] = useState<Location[]>([]);
  const [showNew, setShowNew] = useState(false);
  const [manageFor, setManageFor] = useState<Location | null>(null);

  const load = useCallback(async () => {
    try {
      setLocations(await api.listLocations());
    } catch {
      toast.error("Kan locaties niet laden");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleDelete(l: Location) {
    if (!confirm(`Locatie '${l.code}' verwijderen?`)) return;
    try {
      await api.deleteLocation(l.id);
      toast.success("Locatie verwijderd");
      load();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout");
    }
  }

  return (
    <>
      <div className="flex justify-between items-center mb-1">
        <h2 className="text-xl font-bold">Locaties</h2>
        <Button size="sm" onClick={() => setShowNew(true)}>
          + Locatie
        </Button>
      </div>
      <p className="text-sm text-muted-foreground mb-4">
        Piklocaties voor barcode-producten. Alleen barcode-producten kunnen aan een locatie.
      </p>

      <div className="space-y-3 mb-8">
        {locations.map((l) => (
          <Card key={l.id} className="p-4">
            <div className="flex justify-between items-start">
              <div>
                <p className="font-semibold flex items-center gap-1.5">
                  <MapPin className="h-4 w-4 text-muted-foreground" />
                  {l.code}
                  {!l.active && <Badge variant="inactive">inactief</Badge>}
                </p>
                {locationSubtitle(l) && (
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {locationSubtitle(l)}
                  </p>
                )}
              </div>
              <Button variant="ghost" size="icon" onClick={() => handleDelete(l)}>
                <Trash2 className="h-4 w-4 text-destructive" />
              </Button>
            </div>

            <div className="mt-3 flex flex-wrap gap-1.5">
              {l.skus.map((s) => (
                <Badge key={s.sku_id} className="font-normal" title={s.sku_code}>
                  {s.name}
                </Badge>
              ))}
              {l.skus.length === 0 && (
                <span className="text-xs text-muted-foreground">
                  Nog geen producten
                </span>
              )}
            </div>

            <Button
              variant="outline"
              size="sm"
              className="mt-3"
              onClick={() => setManageFor(l)}
            >
              Producten beheren
            </Button>
          </Card>
        ))}
        {locations.length === 0 && (
          <p className="text-center text-muted-foreground py-4">Geen locaties</p>
        )}
      </div>

      <NewLocationDialog
        open={showNew}
        onClose={() => setShowNew(false)}
        onCreated={load}
      />
      <ManageSkusDialog
        location={manageFor}
        onClose={() => setManageFor(null)}
        onChanged={load}
      />
    </>
  );
}

function NewLocationDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [code, setCode] = useState("");
  const [rij, setRij] = useState("");
  const [kast, setKast] = useState("");
  const [plank, setPlank] = useState("");

  useEffect(() => {
    if (open) {
      setCode("");
      setRij("");
      setKast("");
      setPlank("");
    }
  }, [open]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.createLocation({
        code: code.trim(),
        rij: rij.trim() || undefined,
        kast: kast.trim() || undefined,
        plank: plank.trim() || undefined,
      });
      toast.success(`Locatie '${code.trim()}' aangemaakt`);
      onClose();
      onCreated();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout");
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Nieuwe locatie</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label>Locatiecode (scanbaar)</Label>
            <Input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="Bijv. AB123"
              minLength={1}
              required
              autoFocus
            />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-2">
              <Label>Rij</Label>
              <Input value={rij} onChange={(e) => setRij(e.target.value)} placeholder="A" />
            </div>
            <div className="space-y-2">
              <Label>Kast</Label>
              <Input value={kast} onChange={(e) => setKast(e.target.value)} placeholder="B" />
            </div>
            <div className="space-y-2">
              <Label>Plank</Label>
              <Input value={plank} onChange={(e) => setPlank(e.target.value)} placeholder="1" />
            </div>
          </div>
          <Button type="submit" className="w-full">
            Aanmaken
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ManageSkusDialog({
  location,
  onClose,
  onChanged,
}: {
  location: Location | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<AvailableSKU[]>([]);
  // Local copy so the dialog reflects link/unlink without a full page reload.
  const [linked, setLinked] = useState<LocationSKU[]>([]);

  useEffect(() => {
    setLinked(location?.skus ?? []);
    setQuery("");
    setResults([]);
  }, [location]);

  useEffect(() => {
    if (!location) return;
    const term = query.trim();
    if (!term) {
      setResults([]);
      return;
    }
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const r: AvailableSKU[] = await api.availableLocationSkus(term);
        if (!cancelled) setResults(r);
      } catch {
        /* ignore transient search errors */
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [query, location]);

  if (!location) return null;

  const linkedIds = new Set(linked.map((s) => s.sku_id));

  async function add(s: AvailableSKU) {
    try {
      await api.linkLocationSku(location!.id, s.id);
      setLinked((prev) => [
        ...prev,
        {
          sku_id: s.id,
          sku_code: s.sku_code,
          name: s.name,
          ean: s.ean,
          organization_name: s.organization_name,
          is_primary: true,
        },
      ]);
      onChanged();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Kon niet koppelen");
    }
  }

  async function remove(skuId: number) {
    try {
      await api.unlinkLocationSku(location!.id, skuId);
      setLinked((prev) => prev.filter((s) => s.sku_id !== skuId));
      onChanged();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Kon niet ontkoppelen");
    }
  }

  return (
    <Dialog open={!!location} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Producten op {location.code}</DialogTitle>
        </DialogHeader>

        <div className="space-y-2">
          {linked.map((s) => (
            <div
              key={s.sku_id}
              className="flex items-center justify-between rounded-md border p-2"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium truncate" title={s.sku_code}>
                  {s.name}
                </p>
                <p className="text-xs text-muted-foreground font-mono">
                  {s.ean ?? s.sku_code}
                  {s.organization_name ? ` · ${s.organization_name}` : ""}
                </p>
              </div>
              <button
                onClick={() => remove(s.sku_id)}
                className="text-muted-foreground hover:text-destructive shrink-0 ml-2"
                aria-label="Ontkoppelen"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ))}
          {linked.length === 0 && (
            <p className="text-xs text-muted-foreground">Nog geen producten op deze locatie.</p>
          )}
        </div>

        <div className="space-y-2 pt-2 border-t">
          <Label>Product toevoegen (alleen barcode)</Label>
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Zoek op naam, code of EAN…"
          />
          {results.length > 0 && (
            <div className="max-h-56 overflow-y-auto space-y-1">
              {results.map((s) => {
                const already = linkedIds.has(s.id);
                return (
                  <button
                    key={s.id}
                    disabled={already}
                    onClick={() => add(s)}
                    className="w-full text-left rounded-md border p-2 hover:bg-accent disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    <p className="text-sm font-medium truncate">{s.name}</p>
                    <p className="text-xs text-muted-foreground font-mono">
                      {s.ean ?? s.sku_code}
                      {s.organization_name ? ` · ${s.organization_name}` : ""}
                      {already ? " · al gekoppeld" : ""}
                    </p>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
