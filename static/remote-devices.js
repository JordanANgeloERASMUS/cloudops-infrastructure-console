"use strict";

(() => {
    const form = document.getElementById("remote-pair-form");
    const list = document.getElementById("remote-device-list");
    const inbox = document.getElementById("remote-share-inbox");
    const status = document.getElementById("remote-device-status");
    const submit = document.getElementById("remote-pair-submit");
    const tokenInput = document.getElementById("remote-device-token");
    const permissions = window.CLOUDOPS_ACCESS || {};
    const csrfToken =
        document.querySelector('meta[name="csrf-token"]')?.content || "";
    const metricsCache = new Map();
    let refreshInProgress = false;
    let inboxRefreshInProgress = false;

    if (!form || !list || !inbox) return;

    function setStatus(message, isError = false) {
        status.textContent = message;
        status.classList.toggle("is-error", isError);
    }

    async function request(url, options = {}) {
        const response = await fetch(url, {
            cache: "no-store",
            credentials: "same-origin",
            ...options
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.error || `Request failed (${response.status})`);
        }
        return data;
    }

    async function mutate(url, method, body) {
        return request(url, {
            method,
            headers: {
                "Content-Type": "application/json",
                "X-CSRF-Token": csrfToken
            },
            body: body === undefined ? undefined : JSON.stringify(body)
        });
    }

    function addText(parent, tag, className, value) {
        const element = document.createElement(tag);
        element.className = className;
        element.textContent = value ?? "—";
        parent.appendChild(element);
        return element;
    }

    function formatBytes(value) {
        let bytes = Number(value);
        if (!Number.isFinite(bytes) || bytes < 0) return "—";
        const units = ["B", "KB", "MB", "GB", "TB"];
        let unit = 0;
        while (bytes >= 1024 && unit < units.length - 1) {
            bytes /= 1024;
            unit += 1;
        }
        return `${bytes.toFixed(unit ? 1 : 0)} ${units[unit]}`;
    }

    function renderShareOffer(share) {
        const card = document.createElement("article");
        card.className = "remote-share-offer";
        const details = document.createElement("div");
        addText(details, "strong", "", share.filename);
        addText(
            details,
            "small",
            "",
            `${share.sender} · ${formatBytes(share.size)} · awaiting your approval`
        );
        card.appendChild(details);

        const actions = document.createElement("div");
        actions.className = "remote-share-actions";
        [["approve", "Approve & receive"], ["reject", "Reject"]].forEach(
            ([decision, label]) => {
                const button = document.createElement("button");
                button.type = "button";
                button.className = decision === "approve"
                    ? "fm-button fm-primary"
                    : "fm-button";
                button.dataset.shareDecision = decision;
                button.dataset.shareId = share.id;
                button.textContent = label;
                actions.appendChild(button);
            }
        );
        card.appendChild(actions);
        return card;
    }

    async function refreshInbox() {
        if (inboxRefreshInProgress || !permissions.share_files) return;
        inboxRefreshInProgress = true;
        try {
            const { shares } = await request("/api/shares/pending");
            inbox.replaceChildren();
            if (!shares.length) {
                addText(inbox, "div", "remote-empty", "No incoming file offers.");
            } else {
                shares.forEach(share => inbox.appendChild(renderShareOffer(share)));
            }
        } catch (error) {
            inbox.replaceChildren();
            addText(inbox, "div", "remote-empty is-error", error.message);
        } finally {
            inboxRefreshInProgress = false;
        }
    }

    async function sendFile(deviceId, file, input) {
        if (!permissions.share_files) {
            setStatus("Enable file sharing in Access settings first.", true);
            input.value = "";
            return;
        }
        if (file.size === 0 || file.size > 10 * 1024 * 1024) {
            setStatus("Choose a non-empty file of 10 MB or smaller.", true);
            input.value = "";
            return;
        }

        const body = new FormData();
        body.append("file", file);
        setStatus(`Sending ${file.name} to the device for approval…`);
        try {
            await request(
                `/api/remote/devices/${encodeURIComponent(deviceId)}/shares`,
                {
                    method: "POST",
                    headers: {"X-CSRF-Token": csrfToken},
                    body
                }
            );
            setStatus(
                `File offer sent. The other device must approve ${file.name} before it is saved.`
            );
        } catch (error) {
            setStatus(error.message, true);
        } finally {
            input.value = "";
        }
    }

    function renderDevice(device) {
        const card = document.createElement("article");
        card.className = "remote-device-card";
        card.dataset.deviceId = device.id;

        const header = document.createElement("div");
        header.className = "remote-device-header";
        const title = document.createElement("div");
        addText(title, "strong", "remote-device-name", device.name);
        addText(
            title,
            "small",
            "remote-device-identity",
            `${device.hostname} · ${device.operating_system} · ${device.host}`
        );
        header.appendChild(title);

        const controls = document.createElement("div");
        controls.className = "remote-device-controls";
        const state = addText(controls, "span", "remote-device-state", "CHECKING");
        state.dataset.deviceState = "";
        const disconnect = document.createElement("button");
        disconnect.type = "button";
        disconnect.className = "fm-button";
        disconnect.dataset.disconnect = device.id;
        disconnect.textContent = "Revoke";
        controls.appendChild(disconnect);
        if (permissions.share_files) {
            const shareButton = document.createElement("button");
            shareButton.type = "button";
            shareButton.className = "fm-button";
            shareButton.dataset.shareFile = device.id;
            shareButton.textContent = "Share file";
            controls.appendChild(shareButton);

            const filePicker = document.createElement("input");
            filePicker.type = "file";
            filePicker.hidden = true;
            filePicker.dataset.sharePicker = device.id;
            controls.appendChild(filePicker);
        }
        header.appendChild(controls);
        card.appendChild(header);

        const metrics = document.createElement("div");
        metrics.className = "remote-device-metrics";
        [
            ["CPU", "cpu"],
            ["Memory", "memory"],
            ["System drive", "disk"],
            ["Network received", "rx"],
            ["Network sent", "tx"]
        ].forEach(([label, key]) => {
            const metric = document.createElement("div");
            metric.className = "remote-device-metric";
            addText(metric, "span", "", label);
            const value = addText(metric, "strong", "", "—");
            value.dataset.metric = key;
            metric.appendChild(value);
            metrics.appendChild(metric);
        });
        card.appendChild(metrics);
        addText(card, "p", "remote-device-updated", "Waiting for telemetry.");

        const cached = metricsCache.get(device.id);
        if (cached) renderMetrics(card, cached);
        return card;
    }

    function renderMetrics(card, metrics) {
        const values = {
            cpu: `${Number(metrics.cpu_usage).toFixed(1)}%`,
            memory: `${Number(metrics.memory_usage).toFixed(1)}%`,
            disk: `${Number(metrics.disk_usage).toFixed(1)}%`,
            rx: formatBytes(metrics.network?.bytes_received),
            tx: formatBytes(metrics.network?.bytes_sent)
        };
        Object.entries(values).forEach(([key, value]) => {
            const element = card.querySelector(`[data-metric="${key}"]`);
            if (element) element.textContent = value;
        });
        const state = card.querySelector("[data-device-state]");
        if (state) {
            state.textContent = "ONLINE";
            state.classList.remove("is-error");
        }
        const updated = card.querySelector(".remote-device-updated");
        const disk = metrics.disk_mountpoint
            ? `System volume ${metrics.disk_mountpoint}`
            : "System volume";
        updated.textContent =
            `${disk} · ${metrics.uptime?.formatted || "Uptime unavailable"} · Updated ${new Date().toLocaleTimeString()}`;
    }

    function renderOffline(card, message) {
        const state = card.querySelector("[data-device-state]");
        if (state) {
            state.textContent = "OFFLINE";
            state.classList.add("is-error");
        }
        const updated = card.querySelector(".remote-device-updated");
        if (updated) updated.textContent = message;
    }

    async function updateDevice(card) {
        const id = card.dataset.deviceId;
        try {
            const metrics = await request(
                `/api/remote/devices/${encodeURIComponent(id)}/metrics`
            );
            metricsCache.set(id, metrics);
            renderMetrics(card, metrics);
        } catch (error) {
            renderOffline(card, error.message);
        }
    }

    async function refreshDevices() {
        if (refreshInProgress || !permissions.remote_access) return;
        refreshInProgress = true;
        try {
            const { devices } = await request("/api/remote/devices");
            list.replaceChildren();
            if (!devices.length) {
                addText(
                    list,
                    "div",
                    "remote-empty",
                    "No devices paired. Set up the Windows agent, then authorize it here."
                );
                return;
            }

            devices.forEach(device => list.appendChild(renderDevice(device)));
            await Promise.all(
                [...list.querySelectorAll(".remote-device-card")]
                    .map(updateDevice)
            );
        } catch (error) {
            list.replaceChildren();
            addText(list, "div", "remote-empty is-error", error.message);
        } finally {
            refreshInProgress = false;
        }
    }

    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (!permissions.remote_access) {
            setStatus("Grant remote device access in Access settings.", true);
            return;
        }

        submit.disabled = true;
        setStatus("Verifying the device key and reading its first metrics...");
        const token = tokenInput.value;
        try {
            await mutate("/api/remote/devices", "POST", {
                name: document.getElementById("remote-device-name").value,
                host: document.getElementById("remote-device-host").value,
                token
            });
            form.reset();
            setStatus("Device authorized. Its access key is held in memory until CloudOps exits.");
            await refreshDevices();
        } catch (error) {
            setStatus(error.message, true);
        } finally {
            tokenInput.value = "";
            submit.disabled = false;
        }
    });

    list.addEventListener("click", async event => {
        const shareButton = event.target.closest("button[data-share-file]");
        if (shareButton) {
            const picker = shareButton.parentElement.querySelector(
                "input[data-share-picker]"
            );
            picker?.click();
            return;
        }

        const button = event.target.closest("button[data-disconnect]");
        if (!button) return;
        if (!window.confirm(
            "Revoke this CloudOps pairing? The remote agent will stop being queried. Rotate its access key on the device if it should no longer be trusted."
        )) return;

        button.disabled = true;
        try {
            await mutate(
                `/api/remote/devices/${encodeURIComponent(button.dataset.disconnect)}`,
                "DELETE"
            );
            metricsCache.delete(button.dataset.disconnect);
            setStatus("Device pairing revoked.");
            await refreshDevices();
        } catch (error) {
            setStatus(error.message, true);
            button.disabled = false;
        }
    });

    list.addEventListener("change", event => {
        const picker = event.target.closest("input[data-share-picker]");
        const file = picker?.files?.[0];
        if (picker && file) {
            sendFile(picker.dataset.sharePicker, file, picker);
        }
    });

    inbox.addEventListener("click", async event => {
        const button = event.target.closest("button[data-share-decision]");
        if (!button) return;
        const decision = button.dataset.shareDecision;
        const prompt = decision === "approve"
            ? "Save this offered file to this device's CloudOps Shared/Received folder? Existing files will not be overwritten."
            : "Reject and discard this file offer?";
        if (!window.confirm(prompt)) return;

        button.disabled = true;
        try {
            const result = await mutate(
                `/api/shares/${encodeURIComponent(button.dataset.shareId)}/decision`,
                "POST",
                {decision}
            );
            setStatus(
                result.status === "received"
                    ? `Received ${result.filename} in CloudOps Shared/Received.`
                    : "File offer rejected."
            );
            await refreshInbox();
        } catch (error) {
            setStatus(error.message, true);
            button.disabled = false;
        }
    });

    if (!permissions.remote_access) {
        form.querySelectorAll("input, button").forEach(control => {
            control.disabled = true;
        });
        setStatus("Grant remote device access in Access settings.");
        list.replaceChildren();
        addText(
            list,
            "div",
            "remote-empty",
            "Remote device access is not enabled for this dashboard session."
        );
        inbox.replaceChildren();
        addText(
            inbox,
            "div",
            "remote-empty",
            "Pair remote devices to use file sharing."
        );
    } else {
        refreshDevices();
        window.setInterval(refreshDevices, 10_000);
        if (permissions.share_files) {
            refreshInbox();
            window.setInterval(refreshInbox, 5_000);
        } else {
            inbox.replaceChildren();
            addText(
                inbox,
                "div",
                "remote-empty",
                "Enable Share selected files in Access settings to receive file offers."
            );
        }
    }
})();
