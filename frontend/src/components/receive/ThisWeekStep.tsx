import { useState, useEffect } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { getISOWeek, shiftWeek } from "./week";
import type { WeeklyPickPhoto } from "./types";

export function ThisWeekStep({ week: initialWeek, onBack }: { week: string; onBack: () => void }) {
  const [week, setWeek] = useState(initialWeek);
  const [items, setItems] = useState<WeeklyPickPhoto[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      try {
        const result = await api.weeklyPickPhotos(week);
        if (!cancelled) setItems(result);
      } catch (err: unknown) {
        if (!cancelled) {
          toast.error(err instanceof Error ? err.message : "Kan weekfoto's niet laden");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [week]);

  return (
    <>
      <div className="flex items-center justify-between gap-3 mb-4">
        <div>
          <h3 className="text-lg font-semibold">Deze week</h3>
          <p className="text-sm text-muted-foreground">{week}</p>
        </div>
        <Button variant="outline" size="sm" onClick={onBack}>
          Terug
        </Button>
      </div>

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

      {loading ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="aspect-square w-full rounded-lg" />
              <Skeleton className="h-4 w-4/5" />
            </div>
          ))}
        </div>
      ) : items.length === 0 ? (
        <p className="text-center text-muted-foreground py-10">
          Geen open pickfoto's voor deze week
        </p>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {items.map((item) => (
            <div key={item.sku_id} className="min-w-0">
              <div className="aspect-square overflow-hidden rounded-lg border border-border bg-muted">
                {item.image_url ? (
                  <img
                    src={item.image_url}
                    alt={item.wine_name}
                    className="h-full w-full object-cover"
                    loading="lazy"
                  />
                ) : (
                  <div className="flex h-full items-center justify-center px-3 text-center text-sm text-muted-foreground">
                    Geen foto
                  </div>
                )}
              </div>
              <p className="mt-2 truncate text-sm font-medium" title={item.wine_name}>
                {item.wine_name}
              </p>
              {item.customers.length > 0 && (
                <p
                  className="truncate text-xs text-muted-foreground"
                  title={item.customers.join(", ")}
                >
                  {item.customers.join(", ")}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
