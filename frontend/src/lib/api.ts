const BASE = "/api";

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

let refreshPromise: Promise<string> | null = null;

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

  // Organizations
  listOrganizations: () => request("/auth/organizations"),
  createOrganization: (data: { name: string; slug: string; custom_label?: string; enabled_modules?: string[]; auto_inactivate_no_images?: boolean }) =>
    json("/auth/organizations", "POST", data),
  updateOrganization: (id: number, data: { name?: string; slug?: string; custom_label?: string | null; enabled_modules?: string[]; auto_inactivate_no_images?: boolean }) =>
    json(`/auth/organizations/${id}`, "PATCH", data),
  deleteOrganization: (id: number) => request(`/auth/organizations/${id}`, { method: "DELETE" }),

  // Suppliers
  listSuppliers: () => request("/suppliers"),
  createSupplier: (data: { name: string }) => json("/suppliers", "POST", data),
  updateSupplier: (id: number, data: { name: string }) => json(`/suppliers/${id}`, "PATCH", data),
  deleteSupplier: (id: number) => request(`/suppliers/${id}`, { method: "DELETE" }),

  // SKUs
  listSKUs: (activeOnly = false) =>
    request(`/skus${activeOnly ? "?active_only=true" : ""}`),
  createSKU: (data: {
    sku_code?: string;
    name?: string;
    category?: string;
    attributes: Record<string, string>;
    active?: boolean;
    supplier_id?: number | null;
  }) => json("/skus", "POST", data),
  getSKU: (id: number) => request(`/skus/${id}`),
  updateSKU: (id: number, data: Record<string, unknown>) =>
    json(`/skus/${id}`, "PATCH", data),
  deleteSKU: (id: number, force = false) =>
    request(`/skus/${id}${force ? "?force=true" : ""}`, { method: "DELETE" }),

  // Reference images
  listImages: (skuId: number) => request(`/skus/${skuId}/images`),
  uploadImage: (skuId: number, file: Blob, skipWineCheck = false, skipDuplicateCheck = false) => {
    const fields: Record<string, string> = {};
    if (skipWineCheck) fields.skip_wine_check = "true";
    if (skipDuplicateCheck) fields.skip_duplicate_check = "true";
    if (Object.keys(fields).length > 0) {
      return uploadWithFields(`/skus/${skuId}/images`, file, fields, "image.jpg");
    }
    return upload(`/skus/${skuId}/images`, file, "image.jpg");
  },
  deleteImage: (skuId: number, imageId: number) =>
    request(`/skus/${skuId}/images/${imageId}`, { method: "DELETE" }),

  // Receiving
  identifyBox: (blob: Blob) =>
    upload("/receiving/identify", blob, "scan.jpg"),
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
  createConceptProduct: (supplierCode: string, description?: string) => {
    const form = new FormData();
    form.append("supplier_code", supplierCode);
    if (description) form.append("description", description);
    return request("/receiving/concept-product", { method: "POST", body: form });
  },

  // Customers
  listCustomers: () => request("/customers"),
  getCustomer: (id: number) => request(`/customers/${id}`),
  createCustomer: (data: { name: string; organization_id?: number | null; show_prices?: boolean; discount_percentage?: number | null; delivery_day?: string }) =>
    json("/customers", "POST", data),
  updateCustomer: (id: number, data: { name?: string; show_prices?: boolean; discount_percentage?: number | null; delivery_day?: string }) =>
    json(`/customers/${id}`, "PATCH", data),
  deleteCustomer: (id: number) => request(`/customers/${id}`, { method: "DELETE" }),
  listCustomerSKUs: (customerId: number) => request(`/customers/${customerId}/skus`),
  addCustomerSKUs: (customerId: number, skuIds: number[]) =>
    json(`/customers/${customerId}/skus`, "POST", { sku_ids: skuIds }),
  removeCustomerSKU: (customerId: number, skuId: number) =>
    request(`/customers/${customerId}/skus/${skuId}`, { method: "DELETE" }),

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
  listOrders: (week?: string) =>
    request(`/orders${week ? `?week=${week}` : ""}`),
  getOrder: (id: number) => request(`/orders/${id}`),
  activateOrder: (id: number) =>
    request(`/orders/${id}/activate`, { method: "POST" }),
  deleteOrder: (id: number) => request(`/orders/${id}`, { method: "DELETE" }),
  listBookings: (orderId: number) => request(`/orders/${orderId}/bookings`),
  weeklyOrderSummary: (week?: string) =>
    request(`/orders/weekly-summary${week ? `?week=${week}` : ""}`),
  getDeadline: (week?: string) =>
    request(`/orders/deadline${week ? `?week=${week}` : ""}`),

  // Receiving - book (1 scan = 1 box = 1 booking)
  bookBox: (blob: Blob, orderId: number) => {
    const form = new FormData();
    form.append("file", blob, "scan.jpg");
    form.append("order_id", String(orderId));
    return request("/receiving/book", { method: "POST", body: form });
  },

  confirmBooking: (token: string, quantity = 1) =>
    request("/receiving/book/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirmation_token: token, quantity }),
    }),

  bookMore: (orderId: number, skuId: number, quantity: number, scanImagePath = "") => {
    const form = new FormData();
    form.append("order_id", String(orderId));
    form.append("sku_id", String(skuId));
    form.append("quantity", String(quantity));
    if (scanImagePath) form.append("scan_image_path", scanImagePath);
    return request("/receiving/book/more", { method: "POST", body: form });
  },

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
  ) => {
    const form = new FormData();
    form.append("file", blob, "shipment.jpg");
    if (supplierName) form.append("supplier_name", supplierName);
    form.append("document_type", documentType);
    return request("/shipments/extract-preview", { method: "POST", body: form });
  },
  createShipment: (data: {
    organization_id?: number | null;
    supplier_name?: string | null;
    reference?: string | null;
    lines: { sku_id: number; quantity: number; supplier_code?: string | null }[];
  }) => json("/shipments", "POST", data),
  bookShipment: (shipmentId: number) =>
    request(`/shipments/${shipmentId}/book`, { method: "POST" }),
  updateDefaultPrice: (skuId: number, defaultPrice: number | null) =>
    json(`/skus/${skuId}/price`, "PUT", { default_price: defaultPrice }),
  updateCustomerPrice: (customerId: number, skuId: number, unitPrice: number | null) =>
    json(`/customers/${customerId}/skus/${skuId}/price`, "PUT", { unit_price: unitPrice }),
  updateCustomerSKUDiscount: (customerId: number, skuId: number, discountType: string | null, discountValue: number | null) =>
    json(`/customers/${customerId}/skus/${skuId}/discount`, "PUT", { discount_type: discountType, discount_value: discountValue }),
  adjustInventory: (skuId: number, quantity: number, note: string | null) =>
    json("/inventory/adjust", "POST", { sku_id: skuId, quantity, note }),

  // Vision (ad-hoc)
  identify: (blob: Blob) => upload("/vision/identify", blob, "scan.jpg"),
};
