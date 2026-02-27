import { useState, useEffect, useCallback } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

interface SKU {
  id: number;
  sku_code: string;
  name: string;
  description: string | null;
  stock_quantity: number;
  active: boolean;
  image_count: number;
}

interface StockMovement {
  id: number;
  sku_id: number;
  sku_code: string;
  sku_name: string;
  quantity: number;
  movement_type: string;
  confidence: number | null;
  notes: string | null;
  username: string;
  created_at: string;
}

type Tab = "stock" | "history";

export function StockPage() {
  const [tab, setTab] = useState<Tab>("stock");

  return (
    <>
      <div className="flex gap-2 mb-4">
        {(
          [
            { id: "stock", label: "Voorraad" },
            { id: "history", label: "Geschiedenis" },
          ] as const
        ).map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-1.5 rounded-full text-sm font-medium border transition-colors ${
              tab === t.id
                ? "bg-primary text-primary-foreground border-primary"
                : "border-border text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "stock" && <StockOverview />}
      {tab === "history" && <MovementHistory />}
    </>
  );
}

function StockOverview() {
  const [skus, setSkus] = useState<SKU[]>([]);

  const load = useCallback(async () => {
    try {
      setSkus(await api.listSKUs());
    } catch {
      toast.error("Kan voorraad niet laden");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const sorted = [...skus].sort((a, b) => a.name.localeCompare(b.name));

  return (
    <>
      <h2 className="text-xl font-bold mb-4">Voorraad overzicht</h2>
      <div className="space-y-3">
        {sorted.length === 0 ? (
          <p className="text-center text-muted-foreground py-10">
            Geen producten gevonden
          </p>
        ) : (
          sorted.map((s) => (
            <Card key={s.id} className="p-4">
              <div className="flex justify-between items-center mb-1">
                <span className="font-semibold">{s.name}</span>
                <Badge
                  variant={s.stock_quantity > 0 ? "active" : "inactive"}
                >
                  {s.stock_quantity} op voorraad
                </Badge>
              </div>
              <p className="text-sm text-muted-foreground">{s.sku_code}</p>
              {s.description && (
                <p className="text-sm text-muted-foreground mt-1">
                  {s.description}
                </p>
              )}
            </Card>
          ))
        )}
      </div>
    </>
  );
}

function MovementHistory() {
  const [movements, setMovements] = useState<StockMovement[]>([]);

  const load = useCallback(async () => {
    try {
      setMovements(await api.receivingHistory());
    } catch {
      toast.error("Kan geschiedenis niet laden");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function formatDate(iso: string) {
    const d = new Date(iso);
    return d.toLocaleDateString("nl-NL", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  const typeLabel: Record<string, string> = {
    received: "Ontvangen",
    adjusted: "Correctie",
    shipped: "Verzonden",
  };

  return (
    <>
      <h2 className="text-xl font-bold mb-4">Ontvangst geschiedenis</h2>
      <div className="space-y-3">
        {movements.length === 0 ? (
          <p className="text-center text-muted-foreground py-10">
            Nog geen ontvangsten
          </p>
        ) : (
          movements.map((m) => (
            <Card key={m.id} className="p-4">
              <div className="flex justify-between items-center mb-1">
                <span className="font-semibold">{m.sku_name}</span>
                <Badge variant="active">+{m.quantity}</Badge>
              </div>
              <p className="text-sm text-muted-foreground">
                {m.sku_code} &bull; {typeLabel[m.movement_type] || m.movement_type}
              </p>
              <p className="text-sm text-muted-foreground mt-1">
                {m.username} &bull; {formatDate(m.created_at)}
                {m.confidence != null && (
                  <> &bull; {Math.round(m.confidence * 100)}% match</>
                )}
              </p>
              {m.notes && (
                <p className="text-sm text-muted-foreground mt-1 italic">
                  {m.notes}
                </p>
              )}
            </Card>
          ))
        )}
      </div>
    </>
  );
}
