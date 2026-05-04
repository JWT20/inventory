import { useState, useEffect, useCallback } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Trash2 } from "lucide-react";

interface Supplier {
  id: number;
  name: string;
  created_at: string;
}

export function SuppliersPage() {
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [showNew, setShowNew] = useState(false);

  const load = useCallback(async () => {
    try {
      const s = await api.listSuppliers();
      setSuppliers(s);
    } catch {
      toast.error("Kan leveranciers niet laden");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleDelete(s: Supplier) {
    if (!confirm(`Leverancier '${s.name}' verwijderen?`)) return;
    try {
      await api.deleteSupplier(s.id);
      toast.success("Leverancier verwijderd");
      load();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Fout");
    }
  }

  return (
    <>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Leveranciers</h2>
        <Button size="sm" onClick={() => setShowNew(true)}>
          + Leverancier
        </Button>
      </div>

      <div className="space-y-3 mb-8">
        {suppliers.map((s) => (
          <Card key={s.id} className="p-4">
            <div className="flex justify-between items-center">
              <p className="font-semibold">{s.name}</p>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => handleDelete(s)}
              >
                <Trash2 className="h-4 w-4 text-destructive" />
              </Button>
            </div>
          </Card>
        ))}
        {suppliers.length === 0 && (
          <p className="text-center text-muted-foreground py-4">
            Geen leveranciers
          </p>
        )}
      </div>

      <NewSupplierDialog
        open={showNew}
        onClose={() => setShowNew(false)}
        onCreated={load}
      />
    </>
  );
}

function NewSupplierDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");

  useEffect(() => {
    if (open) setName("");
  }, [open]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.createSupplier({ name: name.trim() });
      toast.success(`Leverancier '${name.trim()}' aangemaakt`);
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
          <DialogTitle>Nieuwe leverancier</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label>Naam</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Bijv. Domaine Leflaive"
              minLength={1}
              required
              autoFocus
            />
          </div>
          <Button type="submit" className="w-full">
            Aanmaken
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
