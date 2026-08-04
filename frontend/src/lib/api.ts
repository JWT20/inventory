const BASE = "/api";

export interface AdviceProductSyncSummary {
  received: number;
  created: number;
  updated: number;
  deactivated: number;
  conflicts: string[];
}

export function adviceSyncConflictMessage(
  summary: AdviceProductSyncSummary,
): string | null {
  if (summary.conflicts.length === 0) return null;
  const label = summary.conflicts.length === 1 ? "conflict" : "conflicten";
  return `${summary.conflicts.length} ${label}: ${summary.conflicts[0]}`;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown, message: string) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

function getToken(): string | null {
  return localStorage.getItem("token");
}

export function setToken(token: string) {
  localStorage.setItem("token", token);
}

export function clearToken() {
  localStorage.removeItem("token");
  localStorage.removeItem("refresh_token");
}

export function setRefreshToken(token: string) {
  localStorage.setItem("refresh_token", token);
}

function getRefreshToken(): string | null {
  return localStorage.getItem("refresh_token");
}

let refreshPromise: Promise<string | null> | null = null;

async function tryRefresh(): Promise<string | null> {
  const rt = getRefreshToken();
  if (!rt) return null;

  // Deduplicate concurrent refresh calls
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    try {
      const resp = await fetch(`${BASE}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
      });
      if (!resp.ok) {
        clearToken();
        return null;
      }
      const data = await resp.json();
      setToken(data.access_token);
      if (data.refresh_token) {
        setRefreshToken(data.refresh_token);
      }
      return data.access_token as string;
    } catch {
      clearToken();
      return null;
    } finally {
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

async function request(path: string, options: RequestInit = {}) {
  const token = getToken();
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let resp = await fetch(`${BASE}${path}`, { ...options, headers });

  // If access token expired, try refreshing once
  if (resp.status === 401 && getRefreshToken()) {
    const newToken = await tryRefresh();
    if (newToken) {
      headers["Authorization"] = `Bearer ${newToken}`;
      resp = await fetch(`${BASE}${path}`, { ...options, headers });
    }
  }

  if (resp.status === 401) {
    const body = await resp.json().catch(() => ({}));
    clearToken();
    if (path !== "/auth/login") {
      window.location.reload();
    }
    throw new Error(body.detail || "Inloggen mislukt");
  }
  if (resp.status === 204) return null;
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    const detail = body?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message || `Request failed: ${resp.status}`;
    throw new ApiError(resp.status, detail, message);
  }
  const text = await resp.text();
  return text ? JSON.parse(text) : null;
}

async function requestRaw(path: string): Promise<Response> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const resp = await fetch(`${BASE}${path}`, { headers });

  if (resp.status === 401) {
    clearToken();
    window.location.reload();
    throw new Error("Sessie verlopen");
  }
  if (!resp.ok) {
    throw new Error(`Request failed: ${resp.status}`);
  }
  return resp;
}

function json(path: string, method: string, data: unknown) {
  return request(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

function upload(path: string, file: Blob, filename = "file") {
  const form = new FormData();
  form.append("file", file, filename);
  return request(path, { method: "POST", body: form });
}

function uploadWithFields(
  path: string,
  file: Blob,
  fields: Record<string, string>,
  filename = "file",
) {
  const form = new FormData();
  form.append("file", file, filename);
  for (const [k, v] of Object.entries(fields)) {
    form.append(k, v);
  }
  return request(path, { method: "POST", body: form });
}

export const api = {
  // Auth
  login: (username: string, password: string) =>
    json("/auth/login", "POST", { username, password }),
  me: () => request("/auth/me"),
  listUsers: () => request("/auth/users"),
  createUser: (data: { username: string; password: string; role: string; organization_id?: number | null; customer_id?: number | null }) =>
    json("/auth/users", "POST", data),
  deleteUser: (id: number) => request(`/auth/users/${id}`, { method: "DELETE" }),
  resetUserPassword: (userId: number, newPassword: string) =>
    json(`/auth/users/${userId}/password`, "PUT", { new_password: newPassword }),
  changeMyPassword: (currentPassword: string, newPassword: string) =>
    json("/auth/me/password", "PUT", { current_password: currentPassword, new_password: newPassword }),
  logout: (refreshToken: string) =>
    json("/auth/logout", "POST", { refresh_token: refreshToken }),

  // Web Push (one subscription per browser/device).
  pushConfig: () => request("/push/config"),
  pushSubscribe: (data: {
    endpoint: string;
    keys: { p256dh: string; auth: string };
  }) => json("/push/subscriptions", "POST", data),
  pushUnsubscribe: (endpoint: string) =>
    json("/push/subscriptions", "DELETE", { endpoint }),

  // Organizations
  getModuleCatalog: () => request("/auth/modules/catalog"),
  listOrganizations: () => request("/auth/organizations"),
  createOrganization: (data: { name: string; slug: string; custom_label?: string; enabled_modules?: string[]; auto_inactivate_no_images?: boolean }) =>
    json("/auth/organizations", "POST", data),
  updateOrganization: (id: number, data: { name?: string; slug?: string; custom_label?: string | null; enabled_modules?: string[]; auto_inactivate_no_images?: boolean }) =>
    json(`/auth/organizations/${id}`, "PATCH", data),
  deleteOrganization: (id: number) => request(`/auth/organizations/${id}`, { method: "DELETE" }),

  // Channels (Shopify) — platform-admin only
  channelStatus: (orgId: number) =>
    request(`/channels/shopify/status?organization_id=${orgId}`),
  channelConnectUrl: (orgId: number) =>
    request(`/channels/shopify/connect-url?organization_id=${orgId}`),
  channelSync: (orgId: number, full = false) =>
    request(
      `/channels/shopify/sync?organization_id=${orgId}${full ? "&full=true" : ""}`,
      { method: "POST" },
    ),
  channelSetMode: (orgId: number, mode: "observe" | "live") =>
    json(`/channels/shopify/mode?organization_id=${orgId}`, "POST", { mode }),
  channelPushInventory: (orgId: number) =>
    request(`/channels/shopify/push-inventory?organization_id=${orgId}`, {
      method: "POST",
    }),
  channelReconciliation: (orgId: number) =>
    request(`/channels/shopify/reconciliation?organization_id=${orgId}`),
  channelResolveOrder: (
    orderId: number,
    action: "cancel_restock" | "cancel_without_restock",
  ) =>
    json(`/channels/shopify/orders/${orderId}/resolve`, "POST", { action }),

  // Channels (bol) — single server-side account
  bolChannelStatus: (orgId: number) =>
    request(`/channels/bol/status?organization_id=${orgId}`),
  bolChannelConnect: (orgId: number) =>
    request(`/channels/bol/connect?organization_id=${orgId}`, { method: "POST" }),
  bolChannelSync: (orgId: number) =>
    request(`/channels/bol/sync?organization_id=${orgId}`, { method: "POST" }),
  bolChannelSetMode: (orgId: number, mode: "observe" | "live") =>
    json(`/channels/bol/mode?organization_id=${orgId}`, "POST", { mode }),
  bolChannelPushInventory: (orgId: number) =>
    request(`/channels/bol/push-inventory?organization_id=${orgId}`, {
      method: "POST",
    }),
  bolChannelReconciliation: (orgId: number) =>
    request(`/channels/bol/reconciliation?organization_id=${orgId}`),

  // Suppliers
  listSuppliers: () => request("/suppliers"),
  createSupplier: (data: { name: string }) => json("/suppliers", "POST", data),
  updateSupplier: (id: number, data: { name: string }) => json(`/suppliers/${id}`, "PATCH", data),
  deleteSupplier: (id: number) => request(`/suppliers/${id}`, { method: "DELETE" }),

  // Pick locations (courier-only, barcode products)
  listLocations: () => request("/locations"),
  createLocation: (data: { code: string; rij?: string; kast?: string; plank?: string }) =>
    json("/locations", "POST", data),
  updateLocation: (
    id: number,
    data: { code?: string; rij?: string; kast?: string; plank?: string; active?: boolean },
  ) => json(`/locations/${id}`, "PATCH", data),
  deleteLocation: (id: number) => request(`/locations/${id}`, { method: "DELETE" }),
  linkLocationSku: (locationId: number, skuId: number) =>
    json(`/locations/${locationId}/skus`, "POST", { sku_id: skuId }),
  unlinkLocationSku: (locationId: number, skuId: number) =>
    request(`/locations/${locationId}/skus/${skuId}`, { method: "DELETE" }),
  availableLocationSkus: (q: string) =>
    request(`/locations/available-skus?q=${encodeURIComponent(q)}`),

  // SKUs
  syncAdviceProducts: () =>
    json("/skus/advice-sync", "POST", {}) as Promise<AdviceProductSyncSummary>,
  listSKUs: (
    activeOnly = false,
    organizationId?: number,
    opts?: { search?: string; limit?: number; offset?: number },
  ) => {
    const params = new URLSearchParams();
    if (activeOnly) params.set("active_only", "true");
    if (organizationId !== undefined) params.set("organization_id", String(organizationId));
    if (opts?.search) params.set("search", opts.search);
    if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
    if (opts?.offset !== undefined) params.set("offset", String(opts.offset));
    const qs = params.toString();
    return request(`/skus${qs ? `?${qs}` : ""}`);
  },
  // Lightweight SKU list (id, sku_code, name) for dialog pickers — avoids
  // pulling full SKUResponse payloads with attributes/images.
  listSKUOptions: (activeOnly = false, organizationId?: number) => {
    const params = new URLSearchParams();
    if (activeOnly) params.set("active_only", "true");
    if (organizationId !== undefined) params.set("organization_id", String(organizationId));
    const qs = params.toString();
    return request(`/skus/options${qs ? `?${qs}` : ""}`);
  },
  createSKU: (data: {
    sku_code?: string;
    name?: string;
    category?: string;
    attributes: Record<string, string>;
    active?: boolean;
    supplier_id?: number | null;
    is_bottle?: boolean;
    source_product_id?: string | null;
    product_type?: "barcode" | "vision";
    ean?: string;
  }) => json("/skus", "POST", data),
  getSKU: (id: number) => request(`/skus/${id}`),
  updateSKU: (id: number, data: Record<string, unknown>) =>
    json(`/skus/${id}`, "PATCH", data),
  deleteSKU: (id: number, force = false) =>
    request(`/skus/${id}${force ? "?force=true" : ""}`, { method: "DELETE" }),
  // Reference images
  listImages: (skuId: number) => request(`/skus/${skuId}/images`),
  listImageStatuses: (skuId: number) => request(`/skus/${skuId}/images/status`),
  uploadImage: (skuId: number, file: Blob, skipWineCheck = false, skipDuplicateCheck = false) => {
    const fields: Record<string, string> = {};
    if (skipWineCheck) fields.skip_wine_check = "true";
    if (skipDuplicateCheck) fields.skip_duplicate_check = "true";
    if (Object.keys(fields).length > 0) {
      return uploadWithFields(`/skus/${skuId}/images`, file, fields, "image.jpg");
    }
    return upload(`/skus/${skuId}/images`, file, "image.jpg");
  },
  retryImageProcessing: (skuId: number, imageId: number, skipWineCheck = false, skipDuplicateCheck = false) => {
    const fields: Record<string, string> = {};
    if (skipWineCheck) fields.skip_wine_check = "true";
    if (skipDuplicateCheck) fields.skip_duplicate_check = "true";
    const form = new FormData();
    for (const [k, v] of Object.entries(fields)) {
      form.append(k, v);
    }
    return request(`/skus/${skuId}/images/${imageId}/retry`, { method: "POST", body: form });
  },
  deleteImage: (skuId: number, imageId: number) =>
    request(`/skus/${skuId}/images/${imageId}`, { method: "DELETE" }),

  // Receiving
  createNewProduct: (
    blob: Blob,
    skuCode: string,
    name: string,
    description?: string,
  ) => {
    const fields: Record<string, string> = { sku_code: skuCode, name };
    if (description) fields.description = description;
    return uploadWithFields(
      "/receiving/new-product",
      blob,
      fields,
      "image.jpg",
    );
  },
  createConceptProduct: (
    supplierCode: string,
    description?: string,
    isBottle = false,
    organizationId?: number | null,
  ) => {
    const form = new FormData();
    form.append("supplier_code", supplierCode);
    if (description) form.append("description", description);
    form.append("is_bottle", String(isBottle));
    if (organizationId) form.append("organization_id", String(organizationId));
    return request("/receiving/concept-product", { method: "POST", body: form });
  },

  // Customers
  listCustomers: () => request("/customers"),
  getCustomer: (id: number) => request(`/customers/${id}`),
  createCustomer: (data: { name: string; organization_id?: number | null; show_prices?: boolean; discount_percentage?: number | null; delivery_day?: string; delivery_days?: string[] }) =>
    json("/customers", "POST", data),
  updateCustomer: (id: number, data: { name?: string; show_prices?: boolean; discount_percentage?: number | null; delivery_day?: string; delivery_days?: string[] }) =>
    json(`/customers/${id}`, "PATCH", data),
  deleteCustomer: (id: number) => request(`/customers/${id}`, { method: "DELETE" }),
  listCustomerSKUs: (customerId: number) => request(`/customers/${customerId}/skus`),
  addCustomerSKUs: (customerId: number, skuIds: number[]) =>
    json(`/customers/${customerId}/skus`, "POST", { sku_ids: skuIds }),
  removeCustomerSKU: (customerId: number, skuId: number) =>
    request(`/customers/${customerId}/skus/${skuId}`, { method: "DELETE" }),
  reorderCustomerSKUs: (customerId: number, skuIds: number[]) =>
    json(`/customers/${customerId}/skus/reorder`, "PUT", { sku_ids: skuIds }),

  // Orders
  createOrder: (data: {
    organization_id?: number | null;
    remarks?: string;
    lines: {
      customer_id: number;
      sku_id: number;
      quantity: number;
      delivery_day?: string;
    }[];
  }) => json("/orders", "POST", data),
  updateOrder: (id: number, data: { remarks: string }) =>
    json(`/orders/${id}`, "PATCH", data),
  listOrders: (
    week?: string,
    options?: { includeHistory?: boolean; limit?: number },
  ) => {
    const params = new URLSearchParams();
    if (week) params.set("week", week);
    if (options?.includeHistory) params.set("include_history", "true");
    if (options?.limit) params.set("limit", String(options.limit));
    const query = params.toString();
    return request(`/orders${query ? `?${query}` : ""}`);
  },
  getOrder: (id: number) => request(`/orders/${id}`),
  approveOrder: (
    id: number,
    options: { week?: string; deliveryDay?: string; splitUnimaged?: boolean } = {},
  ) =>
    json(`/orders/${id}/approve`, "POST", {
      ...(options.week ? { week: options.week } : {}),
      ...(options.deliveryDay ? { delivery_day: options.deliveryDay } : {}),
      ...(options.splitUnimaged ? { split_unimaged: true } : {}),
    }),
  addOrderLine: (
    orderId: number,
    data: { customer_id: number; sku_id: number; quantity: number; delivery_day?: string },
  ) => json(`/orders/${orderId}/lines`, "POST", data),
  updateOrderLine: (orderId: number, lineId: number, quantity: number) =>
    json(`/orders/${orderId}/lines/${lineId}`, "PATCH", { quantity }),
  deleteOrderLine: (orderId: number, lineId: number) =>
    request(`/orders/${orderId}/lines/${lineId}`, { method: "DELETE" }),
  closeOrder: (id: number) =>
    request(`/orders/${id}/close`, { method: "POST" }),
  deleteOrder: (id: number) => request(`/orders/${id}`, { method: "DELETE" }),
  listBookings: (orderId: number) => request(`/orders/${orderId}/bookings`),
  weeklyPickPhotos: (week?: string) =>
    request(`/orders/weekly-pick-photos${week ? `?week=${week}` : ""}`),
  nextPick: (orderId: number, scanMode: "box" | "bottle" = "box") =>
    request(`/orders/${orderId}/next-pick?scan_mode=${scanMode}`),
  weeklyOrderSummary: (week?: string, groupBy?: "supplier" | "customer") => {
    const params = new URLSearchParams();
    if (week) params.set("week", week);
    if (groupBy) params.set("group_by", groupBy);
    const qs = params.toString();
    return request(`/orders/weekly-summary${qs ? `?${qs}` : ""}`);
  },
  monthlyBookedBoxes: (organizationId?: string) =>
    request(
      `/orders/reports/monthly-boxes${
        organizationId ? `?organization_id=${organizationId}` : ""
      }`,
    ),
  // Picking - barcode/EAN (1 scan = 1 unit booked on the selected order).
  // Resolve the loose Veloyd label, persist its tracking code and return the
  // EAN order the courier should open.
  openOrderByLabel: (labelReference: string) =>
    json("/picking/open-by-label", "POST", {
      label_reference: labelReference,
    }),
  // locationCode is the shelf the courier last scanned; the backend enforces
  // that a located product may only be booked from its own shelf.
  scanEan: (orderId: number, ean: string, locationCode?: string | null) =>
    json("/picking/scan-ean", "POST", {
      order_id: orderId,
      ean,
      location_code: locationCode ?? undefined,
    }),
  // Verify a scanned shelf code and get this order's products that live there.
  scanLocation: (orderId: number, locationCode: string) =>
    json("/picking/scan-location", "POST", {
      order_id: orderId,
      location_code: locationCode,
    }),
  // Undo a single scanned unit (wrong/damaged item).
  undoScan: (bookingId: number) =>
    json("/picking/undo", "POST", { booking_id: bookingId }),
  // Shipping-label verification gate: ships a fully-picked barcode order.
  scanLabel: (orderId: number, labelReference: string) =>
    json("/picking/scan-label", "POST", {
      order_id: orderId,
      label_reference: labelReference,
    }),

  // Receiving - book (1 scan = 1 besteleenheid = 1 booking)
  bookBox: (blob: Blob, orderId: number, scanMode: "box" | "bottle" = "box") => {
    const form = new FormData();
    form.append("file", blob, "scan.jpg");
    form.append("order_id", String(orderId));
    form.append("scan_mode", scanMode);
    return request("/receiving/book", { method: "POST", body: form });
  },

  confirmBooking: (token: string, quantity = 1) =>
    request("/receiving/book/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirmation_token: token, quantity }),
    }),

  bookMore: (orderLineId: number, quantity: number, scanImagePath = "") => {
    const form = new FormData();
    form.append("order_line_id", String(orderLineId));
    form.append("quantity", String(quantity));
    if (scanImagePath) form.append("scan_image_path", scanImagePath);
    return request("/receiving/book/more", { method: "POST", body: form });
  },

  // Receiving - read-only verdeel-lijst: which customers this SKU still needs to go to
  getDistribution: (orderId: number, skuId: number) =>
    request(`/receiving/distribution?order_id=${orderId}&sku_id=${skuId}`),

  registerReferenceAndBook: (registerToken: string, skuId: number) =>
    request("/receiving/register-reference", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ register_token: registerToken, sku_id: skuId }),
    }),

  // Inventory
  listInventoryOverview: (qs = "") => request(`/inventory/overview${qs}`),
  extractShipmentPreview: (
    blob: Blob,
    supplierName = "",
    documentType: "pakbon" | "invoice" | "unknown" = "unknown",
    fileName = "shipment",
  ) => {
    const form = new FormData();
    form.append("file", blob, fileName);
    if (supplierName) form.append("supplier_name", supplierName);
    form.append("document_type", documentType);
    return request("/shipments/extract-preview", { method: "POST", body: form });
  },
  extractShipmentPreviewText: (
    text: string,
    supplierName = "",
    documentType: "pakbon" | "invoice" | "unknown" = "unknown",
  ) =>
    json("/shipments/extract-preview-text", "POST", {
      text,
      supplier_name: supplierName,
      document_type: documentType,
    }),
  createShipment: (data: {
    supplier_name?: string | null;
    reference?: string | null;
    document_sha256?: string | null;
    upload_attempt_id?: number | null;
    force?: boolean;
    lines: { sku_id: number; quantity: number; supplier_code?: string | null }[];
  }) => json("/shipments", "POST", data),
  bookShipment: (shipmentId: number) =>
    request(`/shipments/${shipmentId}/book`, { method: "POST" }),
  listInboundUploads: (limit = 50, offset = 0) =>
    request(`/inbound-uploads?limit=${limit}&offset=${offset}`),
  confirmLineMatch: (data: {
    supplier_name: string;
    supplier_code: string;
    chosen_sku_id: number;
    persist_mapping?: boolean;
  }) =>
    json("/shipments/confirm-line-match", "POST", {
      supplier_name: data.supplier_name,
      supplier_code: data.supplier_code,
      chosen_sku_id: data.chosen_sku_id,
      persist_mapping: data.persist_mapping ?? true,
    }),
  listSupplierMappings: (supplierName?: string) => {
    const qs = supplierName ? `?supplier_name=${encodeURIComponent(supplierName)}` : "";
    return request(`/supplier-mappings${qs}`);
  },
  deleteSupplierMapping: (mappingId: number) =>
    request(`/supplier-mappings/${mappingId}`, { method: "DELETE" }),
  updateDefaultPrice: (skuId: number, defaultPrice: number | null) =>
    json(`/skus/${skuId}/price`, "PUT", { default_price: defaultPrice }),
  updateCustomerPrice: (customerId: number, skuId: number, unitPrice: number | null) =>
    json(`/customers/${customerId}/skus/${skuId}/price`, "PUT", { unit_price: unitPrice }),
  updateCustomerSKUDiscount: (customerId: number, skuId: number, discountType: string | null, discountValue: number | null) =>
    json(`/customers/${customerId}/skus/${skuId}/discount`, "PUT", { discount_type: discountType, discount_value: discountValue }),
  adjustInventory: (
    skuId: number,
    quantity: number,
    note: string | null,
    organizationId: number | null = null,
  ) =>
    json("/inventory/adjust", "POST", {
      sku_id: skuId,
      quantity,
      note,
      organization_id: organizationId,
    }),

  // Vision (ad-hoc)
  identify: (blob: Blob) => upload("/vision/identify", blob, "scan.jpg"),
};
