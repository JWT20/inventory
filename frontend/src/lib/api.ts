const BASE = "/api";

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
    clearToken();
    window.location.reload();
    throw new Error("Sessie verlopen");
  }
  if (resp.status === 204) return null;
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${resp.status}`);
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
  createUser: (data: { username: string; password: string; role: string; organization_id?: number | null }) =>
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
  createOrganization: (data: { name: string; slug: string; enabled_modules?: string[] }) =>
    json("/auth/organizations", "POST", data),
  deleteOrganization: (id: number) => request(`/auth/organizations/${id}`, { method: "DELETE" }),

  // SKUs
  listSKUs: (activeOnly = false) =>
    request(`/skus${activeOnly ? "?active_only=true" : ""}`),
  createSKU: (data: {
    category?: string;
    attributes: Record<string, string>;
    active?: boolean;
  }) => json("/skus", "POST", data),
  getSKU: (id: number) => request(`/skus/${id}`),
  updateSKU: (id: number, data: Record<string, unknown>) =>
    json(`/skus/${id}`, "PATCH", data),
  deleteSKU: (id: number) => request(`/skus/${id}`, { method: "DELETE" }),

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

  // Customers
  listCustomers: () => request("/customers"),
  createCustomer: (name: string) => json("/customers", "POST", { name }),
  deleteCustomer: (id: number) => request(`/customers/${id}`, { method: "DELETE" }),

  // Orders
  createOrder: (data: {
    organization_id?: number | null;
    lines: {
      customer_id: number;
      sku_id: number;
      quantity: number;
    }[];
  }) => json("/orders", "POST", data),
  listOrders: () => request("/orders"),
  getOrder: (id: number) => request(`/orders/${id}`),
  activateOrder: (id: number) =>
    request(`/orders/${id}/activate`, { method: "POST" }),
  deleteOrder: (id: number) => request(`/orders/${id}`, { method: "DELETE" }),
  listBookings: (orderId: number) => request(`/orders/${orderId}/bookings`),

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

  // Vision (ad-hoc)
  identify: (blob: Blob) => upload("/vision/identify", blob, "scan.jpg"),
};
