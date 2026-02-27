const API = (() => {
    const BASE = '/api';

    async function request(path, options = {}) {
        const resp = await fetch(`${BASE}${path}`, options);
        if (resp.status === 204) return null;
        if (!resp.ok) {
            const body = await resp.json().catch(() => ({}));
            throw new Error(body.detail || `Request failed: ${resp.status}`);
        }
        const text = await resp.text();
        return text ? JSON.parse(text) : null;
    }

    function json(path, method, data) {
        return request(path, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
    }

    return {
        // SKUs
        listSKUs: (activeOnly = false) =>
            request(`/skus${activeOnly ? '?active_only=true' : ''}`),
        createSKU: (data) => json('/skus', 'POST', data),
        getSKU: (id) => request(`/skus/${id}`),
        updateSKU: (id, data) => json(`/skus/${id}`, 'PATCH', data),
        deleteSKU: (id) => request(`/skus/${id}`, { method: 'DELETE' }),

        // Reference images
        listImages: (skuId) => request(`/skus/${skuId}/images`),
        uploadImage: (skuId, file) => {
            const form = new FormData();
            form.append('file', file);
            return request(`/skus/${skuId}/images`, { method: 'POST', body: form });
        },
        deleteImage: (skuId, imageId) =>
            request(`/skus/${skuId}/images/${imageId}`, { method: 'DELETE' }),

        // Orders
        listOrders: (status = '') =>
            request(`/orders${status ? `?status=${status}` : ''}`),
        createOrder: (data) => json('/orders', 'POST', data),
        getOrder: (id) => request(`/orders/${id}`),
        deleteOrder: (id) => request(`/orders/${id}`, { method: 'DELETE' }),
        updateOrderStatus: (id, status) =>
            request(`/orders/${id}/status?status=${status}`, { method: 'PATCH' }),

        // Picks
        validatePick: (orderLineId, imageBlob) => {
            const form = new FormData();
            form.append('file', imageBlob, 'scan.jpg');
            return request(`/picks/validate/${orderLineId}`, {
                method: 'POST',
                body: form,
            });
        },

        // Vision
        identify: (imageBlob) => {
            const form = new FormData();
            form.append('file', imageBlob, 'scan.jpg');
            return request('/vision/identify', { method: 'POST', body: form });
        },

        // Health
        health: () => request('/health'),
    };
})();
