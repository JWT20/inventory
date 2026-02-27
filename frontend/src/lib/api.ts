const BASE = "/api";

function getToken(): string | null {
  return localStorage.getItem("token");
}

export function setToken(token: string) {
  localStorage.setItem("token", token);
}

export function clearToken() {
  localStorage.removeItem("token");
}

async function request(path: string, options: RequestInit = {}) {
  const token = getToken();
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const resp = await fetch(`${BASE}${path}`, { ...options, headers });

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
  createUser: (data: { username: string; password: string; is_admin: boolean }) =>
    json("/auth/users", "POST", data),
  deleteUser: (id: number) => request(`/auth/users/${id}`, { method: "DELETE" }),

  // SKUs
  listSKUs: (activeOnly = false) =>
    request(`/skus${activeOnly ? "?active_only=true" : ""}`),
  createSKU: (data: { sku_code: string; name: string; description?: string }) =>
    json("/skus", "POST", data),
  getSKU: (id: number) => request(`/skus/${id}`),
  updateSKU: (id: number, data: Record<string, unknown>) =>
    json(`/skus/${id}`, "PATCH", data),
  deleteSKU: (id: number) => request(`/skus/${id}`, { method: "DELETE" }),

  // Reference images
  listImages: (skuId: number) => request(`/skus/${skuId}/images`),
  uploadImage: (skuId: number, file: Blob) =>
    upload(`/skus/${skuId}/images`, file, "image.jpg"),
  deleteImage: (skuId: number, imageId: number) =>
    request(`/skus/${skuId}/images/${imageId}`, { method: "DELETE" }),

  // Receiving
  identifyBox: (blob: Blob) =>
    upload("/receiving/identify", blob, "scan.jpg"),
  confirmReceiving: (data: {
    sku_id: number;
    quantity: number;
    confidence?: number;
    scan_image_path?: string;
    notes?: string;
  }) => json("/receiving/confirm", "POST", data),
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
  receivingHistory: (limit = 50) =>
    request(`/receiving/history?limit=${limit}`),

  // Labels
  barcodeUrl: (skuId: number) => `/api/labels/${skuId}/barcode.png`,
  labelPdfUrl: (skuId: number, quantity: number) =>
    `/api/labels/${skuId}/label.pdf?quantity=${quantity}`,
  labelZplUrl: (skuId: number, quantity: number) =>
    `/api/labels/${skuId}/label.zpl?quantity=${quantity}`,

  // Vision (ad-hoc)
  identify: (blob: Blob) => upload("/vision/identify", blob, "scan.jpg"),
};
