const UI = (() => {
    function showToast(message, duration = 3000) {
        const el = document.getElementById('toast');
        el.textContent = message;
        el.classList.remove('hidden');
        setTimeout(() => el.classList.add('hidden'), duration);
    }

    function showModal(id) {
        document.getElementById(id).classList.remove('hidden');
    }

    function hideModal(id) {
        document.getElementById(id).classList.add('hidden');
    }

    function badge(status) {
        const labels = { pending: 'Open', picking: 'Bezig', completed: 'Klaar' };
        return `<span class="badge badge-${status}">${labels[status] || status}</span>`;
    }

    function orderCard(order) {
        const totalItems = order.lines.reduce((s, l) => s + l.quantity, 0);
        const pickedItems = order.lines.reduce((s, l) => s + l.picked_quantity, 0);
        return `
            <div class="card" data-order-id="${order.id}">
                <div class="card-header">
                    <span class="card-title">${esc(order.order_number)}</span>
                    ${badge(order.status)}
                </div>
                <div class="card-meta">${esc(order.customer_name)}</div>
                <div class="card-detail">${pickedItems}/${totalItems} gepickt &bull; ${order.lines.length} regels</div>
            </div>
        `;
    }

    function skuCard(sku) {
        return `
            <div class="card" data-sku-id="${sku.id}">
                <div class="card-header">
                    <span class="card-title">${esc(sku.name)}</span>
                    <span class="badge ${sku.active ? 'badge-completed' : 'badge-pending'}">${sku.active ? 'Actief' : 'Inactief'}</span>
                </div>
                <div class="card-meta">${esc(sku.sku_code)}</div>
                <div class="card-detail">${sku.image_count} referentiebeeld${sku.image_count !== 1 ? 'en' : ''}</div>
            </div>
        `;
    }

    function pickableOrderCard(order) {
        const totalItems = order.lines.reduce((s, l) => s + l.quantity, 0);
        const pickedItems = order.lines.reduce((s, l) => s + l.picked_quantity, 0);
        return `
            <div class="card" data-pick-order-id="${order.id}">
                <div class="card-header">
                    <span class="card-title">${esc(order.order_number)}</span>
                    ${badge(order.status)}
                </div>
                <div class="card-meta">${esc(order.customer_name)} &bull; ${pickedItems}/${totalItems} gepickt</div>
            </div>
        `;
    }

    function emptyState(message) {
        return `<div class="empty-state"><p>${message}</p></div>`;
    }

    function esc(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    return { showToast, showModal, hideModal, badge, orderCard, skuCard, pickableOrderCard, emptyState, esc };
})();
