import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { formatBookedBoxesBottles } from "@/lib/units";
import { DELIVERY_DAY_SHORT } from "./constants";
import type { Order } from "./types";

export function OrderCard({ order: o, onSelect }: { order: Order; onSelect: (order: Order) => void }) {
  const days = [...new Set(o.lines?.map((l) => l.delivery_day) ?? [])];
  const dayOrderMap: Record<string, number> = {
    monday: 0,
    tuesday: 1,
    wednesday: 2,
    thursday: 3,
    friday: 4,
  };
  days.sort((a, b) => (dayOrderMap[a] ?? 9) - (dayOrderMap[b] ?? 9));

  return (
    <Card
      className="p-4 cursor-pointer active:scale-[0.98] transition-transform"
      onClick={() => onSelect(o)}
    >
      <div className="flex justify-between items-center mb-1">
        <span className="font-semibold">{o.reference}</span>
        <div className="flex gap-1 items-center">
          {o.status === "completed" && (
            <Badge variant="active" className="text-xs">
              Te verzenden
            </Badge>
          )}
          <Badge
            variant={o.pick_method === "barcode" ? "default" : "outline"}
            className="text-xs"
          >
            {o.pick_method === "barcode" ? "Scanner" : "Camera"}
          </Badge>
          {days.map((d) => (
            <Badge key={d} variant="secondary" className="text-xs">
              {DELIVERY_DAY_SHORT[d] || d}
            </Badge>
          ))}
        </div>
      </div>
      <p className="text-sm text-muted-foreground">
        {o.customer_name ?? "—"}
      </p>
      <p className="text-sm text-muted-foreground">
        {formatBookedBoxesBottles(
          o.booked_boxes,
          o.total_boxes,
          o.booked_bottles,
          o.total_bottles,
        )}{" "}
        geboekt
      </p>
    </Card>
  );
}
