
"use strict";

/* ========================================
   CLOUDOPS PROCESS MANAGER
======================================== */

(() => {
    const REFRESH_INTERVAL = 5000;

    const elements = {
        total: document.getElementById("pm-total"),
        running: document.getElementById("pm-running"),
        user: document.getElementById("pm-user"),
        updated: document.getElementById("pm-updated"),
        search: document.getElementById("pm-search"),
        sort: document.getElementById("pm-sort"),
        refresh: document.getElementById("pm-refresh"),
        list: document.getElementById("pm-process-list"),
        message: document.getElementById("pm-message"),
        details: document.getElementById("pm-details"),
        terminate: document.getElementById("pm-terminate")
    };

    if (!elements.list || !elements.search) return;

    const permissions = window.CLOUDOPS_ACCESS || {};
    const isWindows = window.CLOUDOPS_PLATFORM === "Windows";

    if (!permissions.monitoring) {
        elements.list.innerHTML =
            '<tr><td colspan="6">Grant system monitoring access in Access settings.</td></tr>';
        elements.refresh.disabled = true;
        if (elements.terminate) elements.terminate.hidden = true;
        return;
    }

    if (!permissions.control_processes || isWindows) {
        elements.terminate.hidden = true;
    }

    const csrfToken =
        document.querySelector('meta[name="csrf-token"]')?.content || "";

    let processes = [];
    let currentUser = "";
    let selectedProcess = null;
    let refreshInProgress = false;
    let detailRequestId = 0;

    function formatBytes(bytes) {
        const value = Number(bytes);

        if (!Number.isFinite(value)) return "Unknown";
        if (value < 1024) return `${value} B`;

        const units = ["KB", "MB", "GB", "TB"];
        let size = value / 1024;
        let index = 0;

        while (size >= 1024 && index < units.length - 1) {
            size /= 1024;
            index++;
        }

        return `${size.toFixed(1)} ${units[index]}`;
    }

    function formatCpu(value) {
        const number = Number(value);
        return Number.isFinite(number) ? `${number.toFixed(1)}%` : "—";
    }

    function createCell(value, className = "") {
        const cell = document.createElement("td");
        cell.textContent = value ?? "—";
        if (className) cell.className = className;
        return cell;
    }

    function setMessage(message) {
        elements.message.textContent = message;
    }

    async function apiGet(endpoint) {
        const response = await fetch(endpoint, {
            method: "GET",
            credentials: "same-origin",
            headers: { Accept: "application/json" }
        });

        const data = await response.json().catch(() => ({}));

        if (!response.ok) {
            throw new Error(data.error || `Request failed (${response.status})`);
        }

        return data;
    }

    async function apiPost(endpoint, body) {
        const response = await fetch(endpoint, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Content-Type": "application/json",
                Accept: "application/json",
                "X-CSRF-Token": csrfToken
            },
            body: JSON.stringify(body)
        });

        const data = await response.json().catch(() => ({}));

        if (!response.ok) {
            throw new Error(data.error || `Request failed (${response.status})`);
        }

        return data;
    }

    function getVisibleProcesses() {
        const query = elements.search.value.trim().toLowerCase();

        return processes.filter((process) => {
            const searchable = [
                process.name,
                process.pid,
                process.username,
                process.status
            ].join(" ").toLowerCase();

            return searchable.includes(query);
        });
    }

    function renderProcesses() {
        const visible = getVisibleProcesses();
        elements.list.replaceChildren();

        if (!visible.length) {
            const row = document.createElement("tr");
            const cell = document.createElement("td");

            cell.colSpan = 6;
            cell.className = "pm-empty";
            cell.textContent = processes.length
                ? "No processes match your search."
                : "No processes available.";

            row.appendChild(cell);
            elements.list.appendChild(row);
            return;
        }

        for (const process of visible) {
            const row = document.createElement("tr");

            if (
                selectedProcess &&
                Number(selectedProcess.pid) === Number(process.pid)
            ) {
                row.classList.add("pm-selected");
            }

            row.appendChild(createCell(process.pid, "pm-muted"));

            const nameCell = createCell(process.name || "Unknown");
            nameCell.classList.add("pm-process-name");
            row.appendChild(nameCell);

            row.appendChild(createCell(process.username || "Unknown"));

            const statusCell = document.createElement("td");
            const status = document.createElement("span");
            const state = String(process.status || "unknown").toLowerCase();
            status.className = "pm-status";

            if (
                state.includes("zombie") ||
                state.includes("dead") ||
                state.includes("stop") ||
                state.includes("trace")
            ) {
                status.classList.add("is-critical");
            } else if (
                state.includes("sleep") ||
                state.includes("idle") ||
                state.includes("wait") ||
                state.includes("disk")
            ) {
                status.classList.add("is-idle");
            } else if (!state.includes("run")) {
                status.classList.add("is-warning");
            }

            status.textContent = process.status || "Unknown";
            statusCell.appendChild(status);
            row.appendChild(statusCell);

            row.appendChild(createCell(formatCpu(process.cpu_percent)));
            row.appendChild(createCell(formatBytes(process.memory_rss)));

            row.addEventListener("click", () => selectProcess(process));
            elements.list.appendChild(row);
        }
    }

    function addDetail(label, value) {
        const wrapper = document.createElement("div");
        wrapper.className = "pm-detail-item";

        const title = document.createElement("span");
        title.textContent = label;

        const content = document.createElement("strong");
        content.textContent =
            value === null || value === undefined || value === ""
                ? "Unavailable"
                : String(value);

        wrapper.append(title, content);
        return wrapper;
    }

    function renderDetails(details) {
        const container = document.createElement("div");
        container.className = "pm-details-list";

        container.append(
            addDetail("Process name", details.name),
            addDetail("Process ID", details.pid),
            addDetail("Owner", details.username),
            addDetail("Status", details.status),
            addDetail("CPU usage", formatCpu(details.cpu_percent)),
            addDetail("Memory usage", formatBytes(details.memory_rss)),
            addDetail("Memory percentage", formatCpu(details.memory_percent)),
            addDetail("Threads", details.threads),
            addDetail("File descriptors", details.file_descriptors),
            addDetail("CPU user time",
                `${Number(details.cpu_user_seconds || 0).toFixed(2)} seconds`),
            addDetail("CPU system time",
                `${Number(details.cpu_system_seconds || 0).toFixed(2)} seconds`),
            addDetail("Executable", details.executable),
            addDetail("Working directory", details.working_directory)
        );

        elements.details.replaceChildren(container);

        elements.terminate.disabled = !(
            details.username === currentUser &&
            Number(details.pid) > 1 &&
            selectedProcess !== null
        );
    }

    async function selectProcess(process) {
        selectedProcess = process;
        const requestId = ++detailRequestId;

        renderProcesses();
        elements.details.textContent = "Loading process details...";
        elements.terminate.disabled = true;

        try {
            const details = await apiGet(
                `/api/processes/${encodeURIComponent(process.pid)}`
            );

            if (
                requestId !== detailRequestId ||
                !selectedProcess ||
                Number(selectedProcess.pid) !== Number(process.pid)
            ) return;

            renderDetails(details);
        } catch (error) {
            if (requestId !== detailRequestId) return;

            elements.details.textContent = error.message;
            elements.terminate.disabled = true;
        }
    }

    async function loadProcesses() {
        if (refreshInProgress) return;

        refreshInProgress = true;

        try {
            const sort = encodeURIComponent(elements.sort.value);
            const data = await apiGet(`/api/processes?sort=${sort}`);

            processes = Array.isArray(data.processes) ? data.processes : [];
            currentUser = data.current_user || "";

            elements.total.textContent = processes.length;

            elements.running.textContent = processes.filter(
                process => process.status === "running"
            ).length;

            elements.user.textContent = currentUser || "Unknown";
            elements.updated.textContent = new Date().toLocaleTimeString();

            renderProcesses();

            if (selectedProcess) {
                const refreshed = processes.find(
                    process =>
                        Number(process.pid) === Number(selectedProcess.pid)
                );

                if (refreshed) {
                    selectedProcess = refreshed;
                    await selectProcess(refreshed);
                } else {
                    selectedProcess = null;
                    detailRequestId++;
                    elements.details.textContent =
                        "The selected process is no longer available.";
                    elements.terminate.disabled = true;
                }
            }

            setMessage(
                `Showing ${getVisibleProcesses().length} of ${processes.length} processes.`
            );
        } catch (error) {
            setMessage(`Unable to load processes: ${error.message}`);
        } finally {
            refreshInProgress = false;
        }
    }

    async function terminateSelectedProcess() {
        if (!selectedProcess) return;

        const allowed =
            selectedProcess.username === currentUser &&
            Number(selectedProcess.pid) > 1;

        if (!allowed) {
            setMessage("This process cannot be terminated from the dashboard.");
            return;
        }

        const confirmed = window.confirm(
            `Are you sure you want to terminate "${selectedProcess.name}" (PID ${selectedProcess.pid})?\n\nOnly eligible processes owned by your current account can be terminated.`
        );

        if (!confirmed) return;

        const target = selectedProcess;
        elements.terminate.disabled = true;
        setMessage(`Requesting termination of PID ${target.pid}...`);

        try {
            const result = await apiPost("/api/processes/terminate", {
                pid: target.pid,
                create_time: target.create_time
            });

            setMessage(result.message || "Termination request sent.");

            selectedProcess = null;
            detailRequestId++;
            elements.details.textContent = "Select a process from the list.";

            await loadProcesses();
        } catch (error) {
            setMessage(`Termination failed: ${error.message}`);
            elements.terminate.disabled = false;
        }
    }

    elements.search.addEventListener("input", renderProcesses);

    elements.sort.addEventListener("change", () => {
        selectedProcess = null;
        detailRequestId++;
        elements.details.textContent = "Select a process from the list.";
        elements.terminate.disabled = true;
        loadProcesses();
    });

    elements.refresh.addEventListener("click", loadProcesses);
    elements.terminate.addEventListener("click", terminateSelectedProcess);

    loadProcesses();
    window.setInterval(loadProcesses, REFRESH_INTERVAL);
})();
