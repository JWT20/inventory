/**
 * The physical stock pools, mirrored from the backend.
 *
 * "warehouse" is the courier-run magazijn where everything arrives, "store" is
 * the merchant's shop shelf and "webshop" is the stock set aside for online
 * orders. Store and webshop are two separate physical places; together they are
 * what the webshop can actually sell.
 */
export type InventoryLocation = "warehouse" | "store" | "webshop";

export const INVENTORY_LOCATIONS: InventoryLocation[] = [
  "warehouse",
  "webshop",
  "store",
];

/** Lowercase, for use inside a sentence ("verplaatst naar winkel"). */
export const LOCATION_LABELS: Record<InventoryLocation, string> = {
  warehouse: "magazijn",
  store: "winkel",
  webshop: "webshop",
};

/** Standalone label, for a heading or a badge. */
export const LOCATION_TITLES: Record<InventoryLocation, string> = {
  warehouse: "Magazijn",
  store: "Winkel",
  webshop: "Webshop",
};
