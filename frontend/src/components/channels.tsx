import { useState, useEffect, useCallback } from "react";
import { toast } from "@/App";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface Org {
  id: number;
  name: string;
  enabled_modules: string[];
}

interface ChannelStatus {
  connected: boolean;
  shop_domain: string | null;
  mode: string | null;
  last_synced_at: string | null;
}

interface ReconRow {
  external_id: string;
  reference: string | null;
  channel_reference: string | null;
  channel_fulfillment_status: string | null;
  ordered_at: string | null;
  status: string | null;
  matched_lines: number;
  unmatched_eans: string[];
}

// Shopify reports "fulfilled" once an order is shipped (from home or by the
// courier). Such orders will be kept out of the pick list at cutover.
function isFulfilled(s: string | null): boolean {
  return s === "fulfilled";
}

interface Reconciliation {
  status: ChannelStatus;
  orders: ReconRow[];
  unmatched_eans: string[];
}

function fmtDate(s: string | null): string {
  if (!s) return "—";
  return new Date(s).toLocaleString("nl-NL", { dateStyle: "short", timeStyle: "short" });
}

export function ChannelsPage() {
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [orgId, setOrgId] = useState<number | null>(null);
  const [recon, setRecon] = useState<Reconciliation | null>(null);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);

  // Only orgs that actually run channel orders can be connected.
  useEffect(() => {
    api
      .listOrganizations()
      .then((all: Org[]) => {
        const channelOrgs = all.filter((o) => o.enabled_modules?.includes("channel_orders"));
        setOrgs(channelOrgs);
        if (channelOrgs.length > 0) setOrgId((cur) => cur ?? channelOrgs[0].id);
      })
      .catch(() => toast.error("Kan organisaties niet laden"));
  }, []);

  const load = useCallback(async () => {
    if (!orgId) return;
    setLoading(true);
    try {
      setRecon(await api.channelReconciliation(orgId));
    } catch {
      toast.error("Kan kanaaloverzicht niet laden");
    } finally {
      setLoading(false);
    }
  }, [orgId]);

  useEffect(() => {
    load();
  }, [load]);

  // Show a toast when we return from the Shopify OAuth redirect.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("shopify") === "connected") {
      toast.success("Shopify gekoppeld");
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  async function connect() {
    if (!orgId) return;
    try {
      const { url } = await api.channelConnectUrl(orgId);
      window.location.href = url; // top-level navigation to Shopify consent
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Kan niet verbinden");
    }
  }

  async function sync(full = false) {
    if (!orgId) return;
    setSyncing(true);
    try {
      const r = await api.channelSync(orgId, full);
      toast.success(
        `Sync klaar: ${r.fetched} opgehaald · ${r.created} nieuw · ${r.unmatched} ongematchte EAN's`,
      );
      await load();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Sync mislukt");
    } finally {
      setSyncing(false);
    }
  }

  const status = recon?.status;

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-bold">Kanalen — Shopify</h2>

      {orgs.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Geen organisaties met de kanaal-module. Zet de module aan onder Beheer.
        </p>
      ) : (
        <>
          {orgs.length > 1 && (
            <Select value={orgId ? String(orgId) : ""} onValueChange={(v) => setOrgId(Number(v))}>
              <SelectTrigger className="w-72">
                <SelectValue placeholder="Kies organisatie" />
              </SelectTrigger>
              <SelectContent>
                {orgs.map((o) => (
                  <SelectItem key={o.id} value={String(o.id)}>
                    {o.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}

          <Card className="p-4">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div>
                <p className="text-sm font-semibold flex items-center gap-2">
                  {status?.connected ? (
                    <>
                      <Badge variant="default">Verbonden</Badge>
                      {status.shop_domain}
                    </>
                  ) : (
                    <Badge variant="outline">Niet verbonden</Badge>
                  )}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  {status?.connected
                    ? `Modus: ${status.mode ?? "observe"} · laatst gesynct: ${fmtDate(status.last_synced_at)}`
                    : "Koppel de Shopify-winkel om orders op te halen."}
                </p>
              </div>
              <div className="flex gap-2">
                <Button variant="outline" onClick={connect}>
                  {status?.connected ? "Opnieuw verbinden" : "Verbind Shopify"}
                </Button>
                <Button onClick={() => sync()} disabled={!status?.connected || syncing}>
                  {syncing ? "Synchroniseren…" : "Nu synchroniseren"}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => sync(true)}
                  disabled={!status?.connected || syncing}
                  title="Haalt de hele historie opnieuw op en vult ontbrekende ordernummers/status aan."
                >
                  Volledige hersync
                </Button>
              </div>
            </div>
          </Card>

          {recon && recon.unmatched_eans.length > 0 && (
            <Card className="p-4 bg-amber-50 border-amber-200">
              <p className="text-sm font-semibold text-amber-800 mb-1">
                {recon.unmatched_eans.length} EAN('s) zonder product in de catalogus
              </p>
              <p className="text-xs text-amber-700 font-mono break-all">
                {recon.unmatched_eans.join(", ")}
              </p>
            </Card>
          )}

          <Card className="p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-border">
              <p className="text-sm font-semibold">
                Binnengehaalde orders (observe){loading ? " — laden…" : ""}
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                Het ordernummer is wat Veloyd als referentie op het verzendlabel zet.
                Bij de latere labelscan tijdens het picken matchen we hierop.
                Verzonden orders komen bij de cutover niet in de picklijst.
              </p>
            </div>
            {recon && recon.orders.length > 0 ? (
              <div className="divide-y divide-border">
                {recon.orders.map((o) => (
                  <div key={o.external_id} className="px-4 py-3 flex justify-between items-center gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium">
                        Order {o.channel_reference ?? "—"}
                        {o.status && (
                          <Badge variant="secondary" className="ml-2 text-xs">{o.status}</Badge>
                        )}
                        {isFulfilled(o.channel_fulfillment_status) ? (
                          <Badge variant="outline" className="ml-2 text-xs">verzonden</Badge>
                        ) : (
                          <Badge variant="secondary" className="ml-2 text-xs">nog te picken</Badge>
                        )}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        Shopify-id {o.external_id} · {fmtDate(o.ordered_at)}
                      </p>
                    </div>
                    <div className="text-right text-xs shrink-0">
                      <p className="text-emerald-700">{o.matched_lines} gematcht</p>
                      {o.unmatched_eans.length > 0 && (
                        <p className="text-amber-600">{o.unmatched_eans.length} ontbreken</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="px-4 py-8 text-center text-sm text-muted-foreground">
                Nog geen orders binnengehaald.
              </p>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
