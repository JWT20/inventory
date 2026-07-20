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
  order_id: number | null;
  external_id: string;
  reference: string | null;
  channel_reference: string | null;
  channel_fulfillment_status: string | null;
  review_reason: string | null;
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

// Friendly labels for the imported-order status (mirrors the order overview).
const ORDER_STATUS_LABELS: Record<string, string> = {
  observed: "Observe",
  pending_product: "Wacht op product",
  needs_review: "Handmatige controle",
  active: "Actief",
  completed: "Voltooid",
  shipped: "Verzonden",
  cancelled: "Geannuleerd",
  closed: "Gesloten",
};

function reviewReason(o: ReconRow): string {
  const labels: Record<string, string> = {
    cancelled_after_pick: "Geannuleerd in Shopify nadat het picken begon",
    non_fulfillable_after_pick: "Niet meer te verzenden in Shopify nadat het picken begon",
    fulfilled_elsewhere: "Elders verzonden",
    partially_fulfilled: "Gedeeltelijk elders verzonden; resterende regels zijn nog onbekend",
    unknown_ean: "Onbekend product tijdens picken",
    order_changed_after_pick: "Product of aantal gewijzigd nadat het picken begon",
    fulfillment_reversed: "Shopify-verzending is teruggedraaid; controleer of voorraad terugkwam",
    fulfillment_quantity_mismatch: "Shopify-status en resterende aantallen spreken elkaar tegen",
  };
  return o.review_reason ? (labels[o.review_reason] ?? o.review_reason) : "Reden onbekend";
}

function canResolveCancellation(o: ReconRow): boolean {
  return (
    o.review_reason === "cancelled_after_pick" ||
    o.review_reason === "non_fulfillable_after_pick"
  );
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
  const [bolRecon, setBolRecon] = useState<Reconciliation | null>(null);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [bolConnecting, setBolConnecting] = useState(false);
  const [bolSyncing, setBolSyncing] = useState(false);
  const [changingMode, setChangingMode] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [resolving, setResolving] = useState(false);

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
      const [shopify, bol] = await Promise.all([
        api.channelReconciliation(orgId),
        api.bolChannelReconciliation(orgId),
      ]);
      setRecon(shopify);
      setBolRecon(bol);
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

  async function connectBol() {
    if (!orgId) return;
    setBolConnecting(true);
    try {
      await api.bolChannelConnect(orgId);
      toast.success("bol gekoppeld in observe-modus");
      await load();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Kan bol niet koppelen");
    } finally {
      setBolConnecting(false);
    }
  }

  async function syncBol() {
    if (!orgId) return;
    setBolSyncing(true);
    try {
      const r = await api.bolChannelSync(orgId);
      toast.success(
        `bol-sync klaar: ${r.fetched} opgehaald · ${r.created} nieuw · ${r.unmatched} ongematchte EAN's`,
      );
      await load();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "bol-sync mislukt");
    } finally {
      setBolSyncing(false);
    }
  }

  async function setMode(mode: "observe" | "live") {
    if (!orgId) return;
    if (
      mode === "live" &&
      !window.confirm(
        "Shopify live zetten? Vanaf nu worden betaalde orders pickbaar en wordt " +
          "voorraad naar Shopify teruggeschreven. Bestaande observe-orders worden " +
          "opnieuw ingelezen en waar mogelijk geactiveerd.",
      )
    ) {
      return;
    }
    setChangingMode(true);
    try {
      await api.channelSetMode(orgId, mode);
      toast.success(mode === "live" ? "Shopify staat nu live" : "Terug naar observe");
      await load();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Kan modus niet wijzigen");
    } finally {
      setChangingMode(false);
    }
  }

  async function pushInventory() {
    if (!orgId) return;
    setPushing(true);
    try {
      const r = await api.channelPushInventory(orgId);
      toast.success(
        `Voorraad gepusht: ${r.pushed} bijgewerkt · ${r.skipped_no_variant} overgeslagen · ${r.failed} mislukt`,
      );
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Push mislukt");
    } finally {
      setPushing(false);
    }
  }

  async function resolveOrder(
    orderId: number,
    action: "cancel_restock" | "cancel_without_restock",
  ) {
    const restock = action === "cancel_restock";
    if (
      !window.confirm(
        restock
          ? "Order annuleren en alle al gepickte producten terugboeken in de voorraad?"
          : "Order annuleren zonder de al gepickte producten terug te boeken? Kies dit alleen als die producten niet terug in de voorraad liggen.",
      )
    ) {
      return;
    }
    setResolving(true);
    try {
      await api.channelResolveOrder(orderId, action);
      toast.success(
        restock ? "Order geannuleerd en producten teruggeboekt" : "Order geannuleerd",
      );
      await load();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Actie mislukt");
    } finally {
      setResolving(false);
    }
  }

  const status = recon?.status;
  const isLive = status?.mode === "live";
  const blockedCount =
    recon?.orders.filter(
      (o) => o.status === "pending_product" || o.status === "needs_review",
    ).length ?? 0;

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-bold">Kanalen</h2>

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

          <h3 className="text-base font-semibold">Shopify</h3>
          <Card className="p-4">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div>
                <div className="text-sm font-semibold flex items-center gap-2">
                  {status?.connected ? (
                    <>
                      <Badge variant="default">Verbonden</Badge>
                      {status.shop_domain}
                      <Badge variant={isLive ? "default" : "outline"}>
                        {isLive ? "Live" : "Observe"}
                      </Badge>
                    </>
                  ) : (
                    <Badge variant="outline">Niet verbonden</Badge>
                  )}
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  {status?.connected
                    ? isLive
                      ? `Live — orders worden pickbaar en voorraad wordt teruggeschreven · laatst gesynct: ${fmtDate(status.last_synced_at)}`
                      : `Observe — orders worden alleen ingelezen, niets gewijzigd · laatst gesynct: ${fmtDate(status.last_synced_at)}`
                    : "Koppel de Shopify-winkel om orders op te halen."}
                </p>
              </div>
              <div className="flex gap-2 flex-wrap">
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
                {status?.connected &&
                  (isLive ? (
                    <>
                      <Button
                        onClick={pushInventory}
                        disabled={pushing}
                        title="Zet de absolute voorraad van alle EAN-producten in één keer in Shopify."
                      >
                        {pushing ? "Bezig…" : "Voorraad gelijktrekken"}
                      </Button>
                      <Button
                        variant="outline"
                        onClick={() => setMode("observe")}
                        disabled={changingMode}
                      >
                        {changingMode ? "Bezig…" : "Terug naar observe"}
                      </Button>
                    </>
                  ) : (
                    <Button onClick={() => setMode("live")} disabled={changingMode}>
                      {changingMode ? "Live zetten…" : "Zet live"}
                    </Button>
                  ))}
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
              <div className="text-sm font-semibold flex items-center gap-2">
                Binnengehaalde orders{loading ? " — laden…" : ""}
                {blockedCount > 0 && (
                  <Badge variant="outline" className="text-xs border-red-300 bg-red-50 text-red-700">
                    {blockedCount} geblokkeerd
                  </Badge>
                )}
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                Het ordernummer is wat Veloyd als referentie op het verzendlabel zet.
                Bij de latere labelscan tijdens het picken matchen we hierop.
                Verzonden orders komen bij de cutover niet in de picklijst.
              </p>
            </div>
            {recon && recon.orders.length > 0 ? (
              <div className="divide-y divide-border">
                {recon.orders.map((o) => {
                  const blocked =
                    o.status === "pending_product" || o.status === "needs_review";
                  const needsReview = o.status === "needs_review";
                  return (
                  <div key={o.external_id} className="px-4 py-3 flex justify-between items-start gap-3">
                    <div className="min-w-0">
                      <div className="text-sm font-medium">
                        Order {o.channel_reference ?? "—"}
                        {o.status && (
                          <Badge
                            variant="outline"
                            className={
                              blocked
                                ? "ml-2 text-xs border-red-300 bg-red-50 text-red-700"
                                : "ml-2 text-xs"
                            }
                          >
                            {ORDER_STATUS_LABELS[o.status] ?? o.status}
                          </Badge>
                        )}
                        {isFulfilled(o.channel_fulfillment_status) ? (
                          <Badge variant="outline" className="ml-2 text-xs">verzonden</Badge>
                        ) : (
                          <Badge variant="secondary" className="ml-2 text-xs">nog te picken</Badge>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground">
                        Shopify-id {o.external_id} · {fmtDate(o.ordered_at)}
                      </p>
                      {blocked && o.unmatched_eans.length > 0 && (
                        <p className="text-xs text-red-600 mt-1">
                          Onbekend product — voeg toe om te deblokkeren:{" "}
                          <span className="font-mono break-all">
                            {o.unmatched_eans.join(", ")}
                          </span>
                        </p>
                      )}
                      {needsReview && (
                        <div className="mt-1">
                          <p className="text-xs text-red-600">
                            Reden: {reviewReason(o)}
                          </p>
                          {o.order_id != null && canResolveCancellation(o) ? (
                            <div className="flex gap-2 mt-1">
                              <Button
                                variant="outline"
                                className="h-7 px-2 text-xs"
                                disabled={resolving}
                                onClick={() => resolveOrder(o.order_id!, "cancel_restock")}
                              >
                                Annuleren + terugboeken
                              </Button>
                              <Button
                                variant="outline"
                                className="h-7 px-2 text-xs"
                                disabled={resolving}
                                onClick={() =>
                                  resolveOrder(o.order_id!, "cancel_without_restock")
                                }
                              >
                                Annuleren zonder terugboeken
                              </Button>
                            </div>
                          ) : (
                            <p className="text-xs text-muted-foreground mt-1">
                              Veilig geblokkeerd; deze situatie kan hier niet worden hervat.
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                    <div className="text-right text-xs shrink-0">
                      <p className="text-emerald-700">{o.matched_lines} gematcht</p>
                      {o.unmatched_eans.length > 0 && (
                        <p className={blocked ? "text-red-600" : "text-amber-600"}>
                          {o.unmatched_eans.length} ontbreken
                        </p>
                      )}
                    </div>
                  </div>
                  );
                })}
              </div>
            ) : (
              <p className="px-4 py-8 text-center text-sm text-muted-foreground">
                Nog geen orders binnengehaald.
              </p>
            )}
          </Card>

          <h3 className="text-base font-semibold pt-3">bol</h3>
          <Card className="p-4">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div>
                <div className="text-sm font-semibold flex items-center gap-2">
                  {bolRecon?.status.connected ? (
                    <>
                      <Badge variant="default">Verbonden</Badge>
                      <Badge variant="outline">Observe</Badge>
                    </>
                  ) : (
                    <Badge variant="outline">Niet verbonden</Badge>
                  )}
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  {bolRecon?.status.connected
                    ? `Alleen lezen — openstaande FBR/VVB-orders ophalen, niets bij bol wijzigen · laatst gesynct: ${fmtDate(bolRecon.status.last_synced_at)}`
                    : "Koppel het bol-account uit de beveiligde serveromgeving."}
                </p>
              </div>
              <div className="flex gap-2 flex-wrap">
                <Button variant="outline" onClick={connectBol} disabled={bolConnecting}>
                  {bolConnecting
                    ? "Controleren…"
                    : bolRecon?.status.connected
                      ? "Opnieuw controleren"
                      : "Koppel bol"}
                </Button>
                <Button
                  onClick={syncBol}
                  disabled={!bolRecon?.status.connected || bolSyncing}
                >
                  {bolSyncing ? "Synchroniseren…" : "Nu synchroniseren"}
                </Button>
              </div>
            </div>
          </Card>

          {bolRecon && bolRecon.unmatched_eans.length > 0 && (
            <Card className="p-4 bg-amber-50 border-amber-200">
              <p className="text-sm font-semibold text-amber-800 mb-1">
                bol: {bolRecon.unmatched_eans.length} EAN('s) zonder product
              </p>
              <p className="text-xs text-amber-700 font-mono break-all">
                {bolRecon.unmatched_eans.join(", ")}
              </p>
            </Card>
          )}

          <Card className="p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-border">
              <p className="text-sm font-semibold">
                Binnengehaalde bol-orders{loading ? " — laden…" : ""}
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                Observe-modus: deze orders reserveren nog geen voorraad en zijn nog niet pickbaar.
              </p>
            </div>
            {bolRecon && bolRecon.orders.length > 0 ? (
              <div className="divide-y divide-border">
                {bolRecon.orders.map((o) => (
                  <div
                    key={o.external_id}
                    className="px-4 py-3 flex justify-between items-start gap-3"
                  >
                    <div className="min-w-0">
                      <div className="text-sm font-medium">
                        Order {o.channel_reference ?? o.external_id}
                        {o.status && (
                          <Badge variant="outline" className="ml-2 text-xs">
                            {ORDER_STATUS_LABELS[o.status] ?? o.status}
                          </Badge>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground">
                        bol-id {o.external_id} · {fmtDate(o.ordered_at)}
                      </p>
                    </div>
                    <div className="text-right text-xs shrink-0">
                      <p className="text-emerald-700">{o.matched_lines} gematcht</p>
                      {o.unmatched_eans.length > 0 && (
                        <p className="text-amber-600">
                          {o.unmatched_eans.length} ontbreken
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="px-4 py-8 text-center text-sm text-muted-foreground">
                Nog geen bol-orders binnengehaald.
              </p>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
