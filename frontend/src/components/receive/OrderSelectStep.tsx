import { useState, useEffect } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { OrderCard } from "./OrderCard";
import { getISOWeek, shiftWeek } from "./week";
import type { Order } from "./types";

export function OrderSelectStep({
  onSelect,
  onIdentify,
  onThisWeek,
}: {
  onSelect: (order: Order) => void;
  onIdentify: () => void;
  onThisWeek: (week: string) => void;
}) {
  const { user } = useAuth();
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [week, setWeek] = useState(() => getISOWeek(new Date()));

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const all = await api.listOrders(week);
        // Active orders to pick, plus fully-picked barcode channel orders that
        // still need their shipping label scanned ("Te verzenden") — so the
        // label step survives a refresh or navigating away.
        const worklist = all.filter(
          (o: Order) =>
            o.status === "active" ||
            (o.status === "completed" &&
              o.pick_method === "barcode" &&
              (o.channel ?? "manual") !== "manual"),
        );
        worklist.sort((a: Order, b: Order) => {
          // "Te verzenden" (completed) first — they are one step from done.
          const shipA = a.status === "completed" ? 0 : 1;
          const shipB = b.status === "completed" ? 0 : 1;
          if (shipA !== shipB) return shipA - shipB;
          // Then active orders by booked percentage ascending (least progress first).
          const totalA = a.total_boxes + a.total_bottles;
          const totalB = b.total_boxes + b.total_bottles;
          const pctA = totalA > 0 ? (a.booked_boxes + a.booked_bottles) / totalA : 0;
          const pctB = totalB > 0 ? (b.booked_boxes + b.booked_bottles) / totalB : 0;
          return pctA - pctB;
        });
        setOrders(worklist);
      } catch {
        toast.error("Kan orders niet laden");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [week]);

  return (
    <>
      {/* Week navigation */}
      <div className="flex items-center justify-center gap-2 mb-4">
        <Button variant="outline" size="sm" onClick={() => setWeek((w) => shiftWeek(w, -1))}>
          &larr;
        </Button>
        <span className="text-sm font-medium min-w-[7rem] text-center">{week}</span>
        <Button variant="outline" size="sm" onClick={() => setWeek((w) => shiftWeek(w, 1))}>
          &rarr;
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setWeek(getISOWeek(new Date()))}
          className="ml-2"
        >
          Vandaag
        </Button>
      </div>

      <p className="text-sm text-muted-foreground mb-3">
        Kies een actieve order om te scannen
      </p>

      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i} className="p-4">
              <div className="flex justify-between items-center mb-1">
                <Skeleton className="h-5 w-32" />
                <Skeleton className="h-5 w-14 rounded-full" />
              </div>
              <Skeleton className="h-4 w-44 mt-2" />
              <Skeleton className="h-4 w-36 mt-1" />
            </Card>
          ))}
        </div>
      ) : orders.length === 0 ? (
        <p className="text-center text-muted-foreground py-10">
          Geen actieve orders in {week}
        </p>
      ) : (
        <div className="space-y-3">
          {orders.map((o) => (
            <OrderCard key={o.id} order={o} onSelect={onSelect} />
          ))}
        </div>
      )}

      {(user?.role === "courier" || user?.is_platform_admin) && (
        <div className="grid grid-cols-2 gap-2 mt-4">
          <Button
            variant="secondary"
            className="w-full"
            onClick={() => onThisWeek(week)}
          >
            Deze week
          </Button>
          <Button
            variant="secondary"
            className="w-full"
            onClick={onIdentify}
          >
            Scan zonder order
          </Button>
        </div>
      )}
    </>
  );
}
