import { useState } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { Order } from "./types";

type PickupOrder = Pick<Order, "id" | "reference" | "channel_reference" | "customer_name">;

export function PickupInstructions({ order }: { order: PickupOrder }) {
  return (
    <div className="text-center space-y-1">
      <p className="font-semibold">Klaarzetten voor afhalen — Stavangerweg</p>
      <p className="text-xl font-bold">{order.customer_name || "Klant onbekend"}</p>
      <p className="font-mono">{order.channel_reference || order.reference}</p>
    </div>
  );
}

export function PickupReadyStep({ order, onBack }: { order: PickupOrder; onBack: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function collect() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.collectOrder(order.id);
      toast.success("Order afgehaald");
      onBack();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Afhalen registreren mislukt");
      setBusy(false);
    }
  }

  return (
    <Card className="p-4 mb-4 space-y-4">
      <p className="text-lg font-bold text-center">Klaar voor afhalen</p>
      <PickupInstructions order={order} />
      <p className="text-sm text-muted-foreground text-center">
        Laat deze order staan tot de klant de bestelling heeft ontvangen.
      </p>
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
      <Button className="w-full" disabled={busy} onClick={() => void collect()}>
        {busy ? "Registreren…" : "Afgehaald"}
      </Button>
      <Button variant="secondary" className="w-full" disabled={busy} onClick={onBack}>
        Terug naar orders
      </Button>
    </Card>
  );
}
