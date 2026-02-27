(() => {
    // === State ===
    let currentPage = 'orders';
    let currentOrderFilter = '';
    let pickingState = null; // { order, lineIndex }

    // === Navigation ===
    document.querySelectorAll('.nav-btn').forEach((btn) => {
        btn.addEventListener('click', () => navigateTo(btn.dataset.page));
    });

    function navigateTo(page) {
        currentPage = page;
        document.querySelectorAll('.nav-btn').forEach((b) => b.classList.remove('active'));
        document.querySelector(`.nav-btn[data-page="${page}"]`).classList.add('active');
        document.querySelectorAll('.page').forEach((p) => p.classList.remove('active'));
        document.getElementById(`page-${page}`).classList.add('active');

        if (page === 'orders') loadOrders();
        if (page === 'scan') loadPickableOrders();
        if (page === 'skus') loadSKUs();
        if (page !== 'scan') stopPicking();
    }

    // === Orders ===
    async function loadOrders() {
        const container = document.getElementById('orders-list');
        try {
            const orders = await API.listOrders(currentOrderFilter);
            if (orders.length === 0) {
                container.innerHTML = UI.emptyState('Geen orders gevonden');
            } else {
                container.innerHTML = orders.map(UI.orderCard).join('');
            }
            container.querySelectorAll('.card').forEach((card) => {
                card.addEventListener('click', () => showOrderDetail(card.dataset.orderId));
            });
        } catch (e) {
            container.innerHTML = UI.emptyState('Fout bij laden: ' + e.message);
        }
    }

    async function showOrderDetail(orderId) {
        try {
            const order = await API.getOrder(orderId);
            const linesHtml = order.lines.map((l) => `
                <div class="card" style="cursor:default">
                    <div class="card-header">
                        <span class="card-title">${UI.esc(l.sku_name)}</span>
                        <span class="badge badge-${l.status}">${l.picked_quantity}/${l.quantity}</span>
                    </div>
                    <div class="card-meta">${UI.esc(l.sku_code)}</div>
                </div>
            `).join('');

            const content = document.querySelector('#modal-order .modal-content');
            content.innerHTML = `
                <div class="modal-header">
                    <h3>${UI.esc(order.order_number)} - ${UI.esc(order.customer_name)}</h3>
                    <button class="modal-close">&times;</button>
                </div>
                <div style="margin-bottom:12px">${UI.badge(order.status)}</div>
                <div class="list">${linesHtml}</div>
                <div style="display:flex;gap:8px;margin-top:16px">
                    ${order.status !== 'completed' ? `<button class="btn btn-primary" id="btn-start-pick-modal">Start picken</button>` : ''}
                    <button class="btn btn-danger" id="btn-delete-order">Verwijder</button>
                </div>
            `;
            content.querySelector('.modal-close').addEventListener('click', () => UI.hideModal('modal-order'));
            const startBtn = content.querySelector('#btn-start-pick-modal');
            if (startBtn) {
                startBtn.addEventListener('click', () => {
                    UI.hideModal('modal-order');
                    startPicking(order);
                });
            }
            content.querySelector('#btn-delete-order').addEventListener('click', async () => {
                if (confirm('Order verwijderen?')) {
                    await API.deleteOrder(order.id);
                    UI.hideModal('modal-order');
                    loadOrders();
                    UI.showToast('Order verwijderd');
                }
            });
            UI.showModal('modal-order');
        } catch (e) {
            UI.showToast('Fout: ' + e.message);
        }
    }

    // Order filter
    document.querySelectorAll('.filter-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.filter-btn').forEach((b) => b.classList.remove('active'));
            btn.classList.add('active');
            currentOrderFilter = btn.dataset.filter;
            loadOrders();
        });
    });

    // New order
    document.getElementById('btn-new-order').addEventListener('click', () => {
        resetOrderForm();
        UI.showModal('modal-order');
    });

    function resetOrderForm() {
        const content = document.querySelector('#modal-order .modal-content');
        content.innerHTML = `
            <div class="modal-header">
                <h3>Nieuwe Order</h3>
                <button class="modal-close">&times;</button>
            </div>
            <form id="form-order">
                <label>Ordernummer
                    <input type="text" name="order_number" required>
                </label>
                <label>Klant
                    <input type="text" name="customer_name" required>
                </label>
                <div id="order-lines">
                    <h4>Orderregels</h4>
                    <div id="order-lines-list"></div>
                    <button type="button" id="btn-add-line" class="btn btn-secondary btn-small">+ Regel</button>
                </div>
                <button type="submit" class="btn btn-primary" style="margin-top:16px">Opslaan</button>
            </form>
        `;
        content.querySelector('.modal-close').addEventListener('click', () => UI.hideModal('modal-order'));
        content.querySelector('#btn-add-line').addEventListener('click', addOrderLine);
        content.querySelector('#form-order').addEventListener('submit', handleCreateOrder);
        addOrderLine();
    }

    async function addOrderLine() {
        const list = document.getElementById('order-lines-list');
        const skus = await API.listSKUs(true);
        const options = skus.map((s) => `<option value="${UI.esc(s.sku_code)}">${UI.esc(s.name)} (${UI.esc(s.sku_code)})</option>`).join('');
        const row = document.createElement('div');
        row.className = 'order-line-row';
        row.innerHTML = `
            <select name="sku_code" required>${options}</select>
            <input type="number" name="quantity" min="1" value="1" required>
            <button type="button" class="btn btn-danger btn-remove btn-small">&times;</button>
        `;
        row.querySelector('.btn-remove').addEventListener('click', () => row.remove());
        list.appendChild(row);
    }

    async function handleCreateOrder(e) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        form.querySelectorAll('.order-line-row').forEach((row) => {
            lines.push({
                sku_code: row.querySelector('select').value,
                quantity: parseInt(row.querySelector('input[name="quantity"]').value, 10),
            });
        });
        try {
            await API.createOrder({
                order_number: form.order_number.value,
                customer_name: form.customer_name.value,
                lines,
            });
            UI.hideModal('modal-order');
            loadOrders();
            UI.showToast('Order aangemaakt');
        } catch (e) {
            UI.showToast('Fout: ' + e.message);
        }
    }

    // === Picking ===
    async function loadPickableOrders() {
        const container = document.getElementById('pickable-orders');
        try {
            const orders = await API.listOrders();
            const pickable = orders.filter((o) => o.status !== 'completed');
            if (pickable.length === 0) {
                container.innerHTML = UI.emptyState('Geen openstaande orders');
            } else {
                container.innerHTML = pickable.map(UI.pickableOrderCard).join('');
            }
            container.querySelectorAll('.card').forEach((card) => {
                card.addEventListener('click', async () => {
                    const order = await API.getOrder(card.dataset.pickOrderId);
                    startPicking(order);
                });
            });
        } catch (e) {
            container.innerHTML = UI.emptyState('Fout bij laden');
        }
    }

    async function startPicking(order) {
        navigateTo('scan');

        // Update order status to picking
        if (order.status === 'pending') {
            await API.updateOrderStatus(order.id, 'picking');
            order.status = 'picking';
        }

        // Find first unpicked line
        const lineIndex = order.lines.findIndex((l) => l.status !== 'picked');
        if (lineIndex === -1) {
            UI.showToast('Alle items al gepickt!');
            return;
        }

        pickingState = { order, lineIndex };

        document.getElementById('scan-select-order').classList.add('hidden');
        document.getElementById('scan-active').classList.remove('hidden');
        document.getElementById('scan-result').classList.add('hidden');
        document.getElementById('btn-next-item').classList.add('hidden');

        updatePickDisplay();

        try {
            await Camera.start(
                document.getElementById('camera-feed'),
                document.getElementById('camera-canvas')
            );
        } catch (e) {
            UI.showToast('Camera niet beschikbaar: ' + e.message);
        }
    }

    function updatePickDisplay() {
        if (!pickingState) return;
        const { order, lineIndex } = pickingState;
        const line = order.lines[lineIndex];
        const totalLines = order.lines.length;
        const pickedLines = order.lines.filter((l) => l.status === 'picked').length;

        document.getElementById('pick-order-info').innerHTML = `
            <strong>${UI.esc(order.order_number)}</strong> &mdash; ${UI.esc(order.customer_name)}
        `;
        document.getElementById('pick-current-item').innerHTML = `
            <div class="sku-name">${UI.esc(line.sku_name)}</div>
            <div class="sku-detail">${UI.esc(line.sku_code)} &bull; ${line.picked_quantity}/${line.quantity} dozen</div>
        `;
        document.getElementById('pick-progress').textContent =
            `Regel ${lineIndex + 1} van ${totalLines} (${pickedLines} klaar)`;
    }

    function stopPicking() {
        pickingState = null;
        Camera.stop();
        document.getElementById('scan-select-order').classList.remove('hidden');
        document.getElementById('scan-active').classList.add('hidden');
    }

    document.getElementById('btn-stop-picking').addEventListener('click', stopPicking);

    document.getElementById('btn-capture').addEventListener('click', async () => {
        if (!pickingState) return;
        const btn = document.getElementById('btn-capture');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Herkennen...';

        try {
            const blob = await Camera.capture();
            if (!blob) throw new Error('Geen beeld');

            const line = pickingState.order.lines[pickingState.lineIndex];
            const result = await API.validatePick(line.id, blob);

            const resultEl = document.getElementById('scan-result');
            resultEl.classList.remove('hidden', 'correct', 'incorrect', 'unknown');
            resultEl.textContent = result.message;

            if (result.correct) {
                resultEl.classList.add('correct');
                // Refresh order data
                pickingState.order = await API.getOrder(pickingState.order.id);
                updatePickDisplay();

                // Check if there are more items
                const nextIndex = pickingState.order.lines.findIndex((l) => l.status !== 'picked');
                if (nextIndex !== -1) {
                    document.getElementById('btn-next-item').classList.remove('hidden');
                } else {
                    resultEl.textContent += ' - Order compleet!';
                    setTimeout(() => stopPicking(), 2000);
                }
            } else if (result.matched_sku_code) {
                resultEl.classList.add('incorrect');
            } else {
                resultEl.classList.add('unknown');
            }
        } catch (e) {
            UI.showToast('Fout: ' + e.message);
        } finally {
            btn.disabled = false;
            btn.textContent = 'Scan';
        }
    });

    document.getElementById('btn-next-item').addEventListener('click', () => {
        if (!pickingState) return;
        const nextIndex = pickingState.order.lines.findIndex((l) => l.status !== 'picked');
        if (nextIndex !== -1) {
            pickingState.lineIndex = nextIndex;
            updatePickDisplay();
            document.getElementById('scan-result').classList.add('hidden');
            document.getElementById('btn-next-item').classList.add('hidden');
        }
    });

    // === SKUs ===
    async function loadSKUs() {
        const container = document.getElementById('skus-list');
        try {
            const skus = await API.listSKUs();
            if (skus.length === 0) {
                container.innerHTML = UI.emptyState('Geen SKU\'s gevonden');
            } else {
                container.innerHTML = skus.map(UI.skuCard).join('');
            }
            container.querySelectorAll('.card').forEach((card) => {
                card.addEventListener('click', () => openSKUModal(parseInt(card.dataset.skuId)));
            });
        } catch (e) {
            container.innerHTML = UI.emptyState('Fout bij laden: ' + e.message);
        }
    }

    document.getElementById('btn-new-sku').addEventListener('click', () => {
        openSKUModal(null);
    });

    async function openSKUModal(skuId) {
        const title = document.getElementById('modal-sku-title');
        const form = document.getElementById('form-sku');
        const imagesSection = document.getElementById('sku-images');

        form.reset();
        form.id_field?.remove();

        if (skuId) {
            const sku = await API.getSKU(skuId);
            title.textContent = 'SKU Bewerken';
            form.querySelector('[name="sku_code"]').value = sku.sku_code;
            form.querySelector('[name="name"]').value = sku.name;
            form.querySelector('[name="description"]').value = sku.description || '';
            form.querySelector('[name="id"]').value = sku.id;
            imagesSection.classList.remove('hidden');
            loadSKUImages(sku.id);
        } else {
            title.textContent = 'Nieuwe SKU';
            form.querySelector('[name="id"]').value = '';
            imagesSection.classList.add('hidden');
        }

        UI.showModal('modal-sku');
    }

    async function loadSKUImages(skuId) {
        const container = document.getElementById('sku-images-list');
        const images = await API.listImages(skuId);
        if (images.length === 0) {
            container.innerHTML = '<p style="color:var(--text-muted);font-size:0.9rem;">Nog geen referentiebeelden</p>';
        } else {
            container.innerHTML = images.map((img) => `
                <div class="ref-image-thumb">
                    <img src="/api/uploads/reference_images/${skuId}/${img.image_path.split('/').pop()}" alt="ref">
                    <button class="ref-image-delete" data-image-id="${img.id}">&times;</button>
                </div>
            `).join('');
            container.querySelectorAll('.ref-image-delete').forEach((btn) => {
                btn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    await API.deleteImage(skuId, btn.dataset.imageId);
                    loadSKUImages(skuId);
                    UI.showToast('Beeld verwijderd');
                });
            });
        }
    }

    document.getElementById('form-sku').addEventListener('submit', async (e) => {
        e.preventDefault();
        const form = e.target;
        const id = form.querySelector('[name="id"]').value;
        const data = {
            sku_code: form.querySelector('[name="sku_code"]').value,
            name: form.querySelector('[name="name"]').value,
            description: form.querySelector('[name="description"]').value || null,
        };

        try {
            if (id) {
                await API.updateSKU(id, data);
                UI.showToast('SKU bijgewerkt');
            } else {
                const created = await API.createSKU(data);
                form.querySelector('[name="id"]').value = created.id;
                document.getElementById('sku-images').classList.remove('hidden');
                loadSKUImages(created.id);
                UI.showToast('SKU aangemaakt - voeg nu referentiebeelden toe');
            }
        } catch (e) {
            UI.showToast('Fout: ' + e.message);
        }
    });

    document.getElementById('ref-image-input').addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const skuId = document.querySelector('#form-sku [name="id"]').value;
        if (!skuId) return;

        UI.showToast('Beeld uploaden en verwerken...');
        try {
            await API.uploadImage(skuId, file);
            loadSKUImages(skuId);
            UI.showToast('Referentiebeeld toegevoegd');
        } catch (err) {
            UI.showToast('Fout: ' + err.message);
        }
        e.target.value = '';
    });

    // Modal close handlers
    document.querySelectorAll('.modal-close').forEach((btn) => {
        btn.addEventListener('click', () => {
            btn.closest('.modal').classList.add('hidden');
        });
    });

    document.querySelectorAll('.modal').forEach((modal) => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) modal.classList.add('hidden');
        });
    });

    // === Init ===
    loadOrders();
})();
