import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { X } from "lucide-react";
import { DELIVERY_DAY_LABELS } from "./constants";
import type { DistributionResult } from "./types";

/* Verdeel-lijst: which customers this SKU still needs to go to */
export function DistributionPanel({ orderId, skuId, refreshKey }: { orderId: number; skuId: number; refreshKey: number }) {
  const [data, setData] = useState<DistributionResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setFailed(false);
    // Each new box (refreshKey) or SKU is fresh info worth showing again, so a
    // manual dismiss only hides the current list — not every future one.
    setDismissed(false);
    api
      .getDistribution(orderId, skuId)
      .then((res: DistributionResult) => { if (active) setData(res); })
      .catch(() => { if (active) setFailed(true); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
    // refreshKey bumps after each extra booking so the verdeel-lijst re-fetches its
    // gescand/nog counts instead of going stale against the box we just booked.
  }, [orderId, skuId, refreshKey]);

  if (dismissed) return null;

  if (loading) {
    return (
      <Card className="p-4 mb-4">
        <Skeleton className="h-5 w-44 mb-3" />
        <Skeleton className="h-4 w-full mb-2" />
        <Skeleton className="h-4 w-3/4" />
      </Card>
    );
  }

  // Hide silently on failure, or when this SKU only goes to the one customer we
  // just booked — there is nothing extra to distribute. Read-only: never blocks.
  if (failed || !data || data.lines.length <= 1) return null;

  return (
    <Card className="p-4 mb-4">
      <div className="flex items-center justify-between gap-2 mb-3">
        <p className="text-sm font-semibold">Deze SKU ook nog naar</p>
        <div className="flex items-center gap-1 shrink-0">
          <Badge variant="secondary" className="shrink-0">
            nog {data.total_remaining} te verdelen
          </Badge>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7 -mr-1 text-muted-foreground"
            aria-label="Verdeel-lijst sluiten"
            onClick={() => setDismissed(true)}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>
      <ul className="space-y-2">
        {data.lines.map((line) => (
          <li
            key={line.order_line_id}
            className={`flex items-center justify-between gap-2 text-sm ${line.is_context_order ? "opacity-50" : ""}`}
          >
            <div className="min-w-0">
              <p className="font-medium truncate">
                {line.customer_name}
                {line.is_context_order && (
                  <span className="text-muted-foreground font-normal"> (deze doos)</span>
                )}
              </p>
              <p className="text-xs text-muted-foreground">
                {DELIVERY_DAY_LABELS[line.delivery_day] ?? line.delivery_day}
                {" · besteld "}{line.ordered_quantity}
                {" · gescand "}{line.booked_count}
              </p>
            </div>
            {line.is_complete ? (
              <Badge variant="secondary" className="shrink-0">✓ klaar</Badge>
            ) : (
              <Badge className="shrink-0">nog {line.remaining_quantity}</Badge>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}
