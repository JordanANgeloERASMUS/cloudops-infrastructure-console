
"use strict";

// ========================================
// CLOUDOPS DASHBOARD
// ========================================

const REFRESH_INTERVAL = 250;
const STORAGE_REFRESH_INTERVAL = 15000;
const MAX_CHART_HISTORY_SECONDS = 900;
const ROOT_ID = "system";
const ROOT_PATH = window.CLOUDOPS_ROOT_PATH || "/";
const ROOT_LABEL = window.CLOUDOPS_ROOT_LABEL || "/";
const accessPermissions = window.CLOUDOPS_ACCESS || {};
const accessConfigured = Boolean(window.CLOUDOPS_ACCESS_CONFIGURED);
const isWindows = window.CLOUDOPS_PLATFORM === "Windows";

const csrfToken =
    document.querySelector('meta[name="csrf-token"]')?.content || "";

const elements = {
    cpu: document.getElementById("cpu"),
    memory: document.getElementById("memory"),
    disk: document.getElementById("disk"),
    uptime: document.getElementById("uptime"),

    cpuBar: document.getElementById("cpu-bar"),
    memoryBar: document.getElementById("memory-bar"),
    diskBar: document.getElementById("disk-bar"),

    memoryTotal: document.getElementById("memory-total"),
    diskTotal: document.getElementById("disk-total"),

    connectionStatus: document.getElementById("connection-status"),
    apiStatus: document.getElementById("api-status"),
    lastUpdated: document.getElementById("last-updated"),
    refreshButton: document.getElementById("refresh-button"),
    alertCount: document.getElementById("alert-count"),
    alertsList: document.getElementById("alerts-list"),
    monitoringState: document.getElementById("monitoring-state"),
    resourceChart: document.getElementById("resource-chart"),
    networkChart: document.getElementById("network-chart"),
    resourceChartStats: document.getElementById("resource-chart-stats"),
    networkChartStats: document.getElementById("network-chart-stats"),
    consoleToggle: document.getElementById("console-toggle"),
    consolePanel: document.getElementById("console-panel"),
    consoleActions: document.getElementById("console-actions"),
    consoleOutput: document.getElementById("console-output"),

    storageTotal: document.getElementById("storage-total"),
    storageUsed: document.getElementById("storage-used"),
    storagePercent: document.getElementById("storage-used-percent"),
    storageFree: document.getElementById("storage-free"),
    storageDeviceCount: document.getElementById("storage-device-count"),
    storageDeviceStatus: document.getElementById("storage-device-status"),
    storageDevices: document.getElementById("storage-devices"),
    storageRefresh: document.getElementById("storage-refresh-button"),

    browserBack: document.getElementById("browser-back"),
    browserBreadcrumbs: document.getElementById("browser-breadcrumbs"),
    browserEntries: document.getElementById("browser-entries"),
    browserEntryCount: document.getElementById("browser-entry-count"),

    fmBack: document.getElementById("fm-back"),
    fmUp: document.getElementById("fm-up"),
    fmRefresh: document.getElementById("fm-refresh"),
    fmNewFolder: document.getElementById("fm-new-folder"),
    fmNewFile: document.getElementById("fm-new-file"),
    fmUpload: document.getElementById("fm-upload"),
    fmUploadInput: document.getElementById("fm-upload-input"),
    fmBreadcrumbs: document.getElementById("fm-breadcrumbs"),
    fmSearch: document.getElementById("fm-search"),
    fmCount: document.getElementById("fm-count"),
    fmEntries: document.getElementById("fm-entries"),
    fmCurrentPath: document.getElementById("fm-current-path"),

    editorPanel: document.getElementById("fm-editor-panel"),
    editorTitle: document.getElementById("fm-editor-title"),
    editorContent: document.getElementById("fm-editor-content"),
    editorSave: document.getElementById("fm-editor-save"),
    editorClose: document.getElementById("fm-editor-close"),
    editorStatus: document.getElementById("fm-editor-status"),
    editorInfo: document.getElementById("fm-editor-info"),

    previewPanel: document.getElementById("fm-preview-panel"),
    previewTitle: document.getElementById("fm-preview-title"),
    previewImage: document.getElementById("fm-preview-image"),
    previewImageStage: document.getElementById("fm-image-stage"),
    previewFrame: document.getElementById("fm-preview-frame"),
    previewDocumentStage: document.getElementById("fm-document-stage"),
    previewDocumentContent: document.getElementById("fm-document-content"),
    previewEdit: document.getElementById("fm-preview-edit"),
    previewDownload: document.getElementById("fm-preview-download"),
    previewInfo: document.getElementById("fm-preview-info"),
    previewClose: document.getElementById("fm-preview-close"),
    imageDialog: document.getElementById("fm-image-dialog"),
    imageDialogTitle: document.getElementById("fm-image-dialog-title"),
    imageDialogPath: document.getElementById("fm-image-dialog-path"),
    imageDialogImage: document.getElementById("fm-image-dialog-image"),
    imageDialogDownload: document.getElementById("fm-image-dialog-download"),
    imageDialogClose: document.getElementById("fm-image-dialog-close"),

    propertiesPanel: document.getElementById("fm-properties-panel"),
    propertiesContent: document.getElementById("fm-properties-content"),
    propertiesClose: document.getElementById("fm-properties-close"),
    accessDialog: document.getElementById("access-dialog"),
    accessForm: document.getElementById("access-form"),
    accessSettings: document.getElementById("access-settings-button"),
    accessStatus: document.getElementById("access-status"),
    accessMonitoring: document.getElementById("access-monitoring"),
    accessReadFiles: document.getElementById("access-read-files"),
    accessWriteFiles: document.getElementById("access-write-files"),
    accessControlProcesses: document.getElementById("access-control-processes"),
    accessRemoteDevices: document.getElementById("access-remote-devices"),
    accessShareFiles: document.getElementById("access-share-files")
};

let storagePath = ROOT_PATH;
let fileManagerPath = ROOT_PATH;
let fileManagerEntries = [];
let editingPath = null;
let previewFilePath = null;
let storageRequestNumber = 0;
let fileManagerRequestNumber = 0;
const alertSources = new Map();
let acknowledgedAlerts = readAcknowledgedAlerts();
let consoleActionsLoaded = false;
let dashboardRequestInFlight = false;
let chartSamples = [];
let selectedChartRange = 60;
let previousNetworkCounters = null;
let chartAnimationFrame = null;
let chartFrameData = { resource: null, network: null };
let chartTimeLabelCache = null;


// ========================================
// GENERAL HELPERS
// ========================================

function setText(element, value) {
    if (element) {
        element.textContent = value ?? "";
    }
}

function createElement(tag, className = "", text = undefined) {
    const element = document.createElement(tag);

    if (className) {
        element.className = className;
    }

    if (text !== undefined) {
        element.textContent = String(text);
    }

    return element;
}

function formatGB(value) {
    const number = Number(value);

    return Number.isFinite(number)
        ? number.toFixed(2) + " GB"
        : "—";
}

function formatBytes(bytes) {
    const value = Number(bytes);

    if (!Number.isFinite(value) || value < 0) return "—";
    if (value === 0) return "0 B";

    const units = ["B", "KB", "MB", "GB", "TB"];
    const index = Math.min(
        Math.floor(Math.log(value) / Math.log(1024)),
        units.length - 1
    );

    return (
        value / Math.pow(1024, index)
    ).toFixed(2) + " " + units[index];
}

function formatDate(value) {
    if (!value) return "—";

    const date = new Date(value);

    return Number.isNaN(date.getTime())
        ? String(value)
        : date.toLocaleString();
}

function setProgressBar(element, value) {
    if (!element) return;

    const number = Number(value);

    const width = Number.isFinite(number)
        ? Math.min(100, Math.max(0, number))
        : 0;

    element.style.width = width + "%";
    element.classList.remove("is-healthy", "is-warning", "is-critical");

    if (width >= 90) {
        element.classList.add("is-critical");
    } else if (width >= 75) {
        element.classList.add("is-warning");
    } else {
        element.classList.add("is-healthy");
    }
}

function apiUrl(endpoint, params = {}) {
    const url = new URL(endpoint, window.location.origin);

    Object.entries(params).forEach(([key, value]) => {
        url.searchParams.set(key, value ?? "");
    });

    return url;
}

async function apiGet(endpoint, params = {}) {
    const response = await fetch(
        apiUrl(endpoint, params),
        { cache: "no-store" }
    );

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
        throw new Error(
            data.error || `Request failed (${response.status})`
        );
    }

    return data;
}

async function apiPost(endpoint, body) {
    const response = await fetch(endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken
        },
        body: JSON.stringify(body)
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
        throw new Error(
            data.error || `Request failed (${response.status})`
        );
    }

    return data;
}

function openAccessSettings() {
    if (!elements.accessDialog) return;

    if (elements.accessMonitoring) {
        elements.accessMonitoring.checked = Boolean(
            accessPermissions.monitoring
        );
    }
    if (elements.accessReadFiles) {
        elements.accessReadFiles.checked = Boolean(
            accessPermissions.read_files
        );
    }
    if (elements.accessWriteFiles) {
        elements.accessWriteFiles.checked = Boolean(
            accessPermissions.write_files
        );
    }
    if (elements.accessControlProcesses) {
        elements.accessControlProcesses.checked = Boolean(
            accessPermissions.control_processes
        );
    }
    if (elements.accessRemoteDevices) {
        elements.accessRemoteDevices.checked = Boolean(
            accessPermissions.remote_access
        );
    }
    if (elements.accessShareFiles) {
        elements.accessShareFiles.checked = Boolean(accessPermissions.share_files);
    }

    if (!elements.accessDialog.open) elements.accessDialog.showModal();
}

if (elements.accessSettings) {
    elements.accessSettings.addEventListener("click", openAccessSettings);
}

if (elements.accessReadFiles && elements.accessWriteFiles) {
    elements.accessReadFiles.addEventListener("change", () => {
        if (!elements.accessReadFiles.checked) {
            elements.accessWriteFiles.checked = false;
        }
    });
    elements.accessWriteFiles.addEventListener("change", () => {
        if (elements.accessWriteFiles.checked) {
            elements.accessReadFiles.checked = true;
        }
    });
}

if (elements.accessDialog && elements.accessForm) {
    if (!accessConfigured) openAccessSettings();

    elements.accessDialog.addEventListener("cancel", event => {
        if (!accessConfigured) event.preventDefault();
    });

    elements.accessForm.addEventListener("submit", async event => {
        event.preventDefault();
        const permissions = {
            monitoring: Boolean(elements.accessMonitoring?.checked),
            read_files: Boolean(elements.accessReadFiles?.checked),
            write_files: Boolean(elements.accessWriteFiles?.checked),
            control_processes: Boolean(
                elements.accessControlProcesses?.checked
            ),
            remote_access: Boolean(elements.accessRemoteDevices?.checked),
            share_files: Boolean(elements.accessShareFiles?.checked)
        };

        if (permissions.write_files) permissions.read_files = true;
        if (permissions.share_files) permissions.remote_access = true;
        if (elements.accessStatus) {
            elements.accessStatus.textContent = "Saving access choices...";
        }

        const submit = elements.accessForm.querySelector(
            'button[type="submit"]'
        );
        if (submit) submit.disabled = true;

        try {
            await apiPost("/api/permissions", permissions);
            window.location.reload();
        } catch (error) {
            if (elements.accessStatus) {
                elements.accessStatus.textContent = error.message;
            }
        } finally {
            if (submit) submit.disabled = false;
        }
    });
}

function showError(error, prefix = "CloudOps") {
    console.error(prefix + ":", error);
    alert(error.message || String(error));
}

function joinPath(parent, child) {
    if (isRootPath(parent)) {
        const separator = parent.includes("\\") && !parent.includes("/")
            ? "\\"
            : "/";
        return parent.endsWith("/") || parent.endsWith("\\")
            ? parent + child
            : parent + separator + child;
    }

    const separator = parent.includes("\\") && !parent.includes("/")
        ? "\\"
        : "/";
    return parent.replace(/[\\/]+$/, "") + separator + child;
}

function parentPath(path) {
    if (!path || isRootPath(path)) return ROOT_PATH;

    const trimmed = path.replace(/[\\/]+$/, "");
    const index = Math.max(
        trimmed.lastIndexOf("/"),
        trimmed.lastIndexOf("\\")
    );

    if (index < 0) return ROOT_PATH;

    const parent = trimmed.slice(0, index);
    if (/^[A-Za-z]:$/.test(parent)) return parent + "\\";
    return parent || "/";
}

function isRootPath(path) {
    const normalize = value => String(value || "")
        .replace(/[\\/]+$/, "")
        .toLocaleLowerCase();
    return normalize(path) === normalize(ROOT_PATH);
}

function pathPartsWithinRoot(path) {
    const normalizedPath = String(path || "").replace(/\\/g, "/");
    const normalizedRoot = String(ROOT_PATH || "/").replace(/\\/g, "/");
    const rootPrefix = normalizedRoot.endsWith("/")
        ? normalizedRoot
        : normalizedRoot + "/";

    const comparablePath = isWindows
        ? normalizedPath.toLocaleLowerCase()
        : normalizedPath;
    const comparableRootPrefix = isWindows
        ? rootPrefix.toLocaleLowerCase()
        : rootPrefix;
    if (comparablePath.startsWith(comparableRootPrefix)) {
        return normalizedPath.slice(rootPrefix.length).split("/").filter(Boolean);
    }

    const comparableRoot = isWindows
        ? normalizedRoot.toLocaleLowerCase()
        : normalizedRoot;
    if (comparablePath === comparableRoot) {
        return [];
    }

    return normalizedPath.split("/").filter(Boolean);
}

function renderPathBreadcrumbs(container, path, browse) {
    container.replaceChildren();

    const root = createElement("button", "", ROOT_LABEL);
    root.type = "button";
    root.addEventListener("click", () => browse(ROOT_PATH));
    container.appendChild(root);

    let accumulated = ROOT_PATH;
    pathPartsWithinRoot(path).forEach(part => {
        container.appendChild(createElement("span", "", "/"));
        accumulated = joinPath(accumulated, part);

        const button = createElement("button", "", part);
        button.type = "button";
        const destination = accumulated;
        button.addEventListener("click", () => browse(destination));
        container.appendChild(button);
    });
}

function isImageFile(name) {
    return /\.(png|jpe?g|gif|webp|bmp|svg|avif)$/i.test(name);
}

function isPdfFile(name) {
    return /\.pdf$/i.test(name);
}

function isDocxFile(name) {
    return /\.docx$/i.test(name);
}

function entryIcon(entry) {
    if (entry.type === "directory") return "📁";
    if (entry.type === "symlink") return "↗";
    if (entry.type === "other") return "◇";
    if (isImageFile(entry.name)) return "▧";
    return "▤";
}

function isHidden(name) {
    return name.startsWith(".");
}


// ========================================
// CONNECTION STATUS
// ========================================

function setConnectionStatus(online) {
    setText(
        elements.connectionStatus,
        online ? "SYSTEM ONLINE" : "CONNECTION ERROR"
    );

    if (elements.connectionStatus) {
        elements.connectionStatus.classList.toggle("is-online", online);
        elements.connectionStatus.classList.toggle("is-offline", !online);
    }

    if (elements.apiStatus) {
        elements.apiStatus.replaceChildren();

        const indicator = document.createElement("i");
        elements.apiStatus.appendChild(indicator);

        elements.apiStatus.appendChild(
            document.createTextNode(
                online ? " CONNECTED" : " DISCONNECTED"
            )
        );

        elements.apiStatus.classList.toggle("pending", false);
        elements.apiStatus.classList.toggle("is-critical", !online);
    }
}

function updateTimestamp() {
    setText(
        elements.lastUpdated,
        "Last updated: " + new Date().toLocaleTimeString()
    );
}


// ========================================
// DASHBOARD ALERTS
// ========================================

function readAcknowledgedAlerts() {
    try {
        const saved = JSON.parse(
            window.localStorage.getItem("cloudops.alerts.acknowledged") || "[]"
        );
        return new Set(Array.isArray(saved) ? saved : []);
    } catch (error) {
        console.warn("CloudOps alert acknowledgements are unavailable:", error);
        return new Set();
    }
}

function saveAcknowledgedAlerts() {
    try {
        window.localStorage.setItem(
            "cloudops.alerts.acknowledged",
            JSON.stringify([...acknowledgedAlerts])
        );
    } catch (error) {
        console.warn("CloudOps could not save alert acknowledgements:", error);
    }
}

function setAlertSource(source, alerts) {
    alertSources.set(source, Array.isArray(alerts) ? alerts : []);
    renderAlerts();
}

function renderAlerts() {
    if (!elements.alertsList || !elements.alertCount) return;

    const activeAlerts = new Map();
    alertSources.forEach(alerts => {
        alerts.forEach(item => {
            if (item && item.id) activeAlerts.set(item.id, item);
        });
    });

    const retainedAcknowledgements = [...acknowledgedAlerts].filter(
        id => activeAlerts.has(id)
    );
    if (retainedAcknowledgements.length !== acknowledgedAlerts.size) {
        acknowledgedAlerts = new Set(retainedAcknowledgements);
        saveAcknowledgedAlerts();
    }

    const visibleAlerts = [...activeAlerts.values()].filter(
        item => !acknowledgedAlerts.has(item.id)
    );
    const acknowledgedCount = activeAlerts.size - visibleAlerts.length;
    const telemetryReady =
        alertSources.has("metrics") && alertSources.has("storage");
    const hasCritical = [...activeAlerts.values()].some(
        item => item.severity === "critical"
    );
    const hasWarning = [...activeAlerts.values()].some(
        item => item.severity === "warning"
    );

    if (elements.monitoringState) {
        const state = !telemetryReady
            ? "CHECKING"
            : hasCritical
                ? "CRITICAL ALERTS"
                : hasWarning
                    ? "WARNING"
                    : "OPERATIONAL";
        elements.monitoringState.replaceChildren(
            createElement("span", "status-light"),
            document.createTextNode(state)
        );
        elements.monitoringState.classList.toggle(
            "is-warning",
            telemetryReady && hasWarning && !hasCritical
        );
        elements.monitoringState.classList.toggle(
            "is-critical",
            telemetryReady && hasCritical
        );
    }

    setText(
        elements.alertCount,
        !telemetryReady
            ? "Checking..."
            : activeAlerts.size
            ? `${visibleAlerts.length} active · ${acknowledgedCount} acknowledged`
            : "No active alerts"
    );

    elements.alertsList.replaceChildren();
    if (!visibleAlerts.length) {
        const message = !telemetryReady
            ? "Waiting for local metrics and storage telemetry..."
            : activeAlerts.size
                ? "All active alerts are acknowledged. A condition will reappear after it clears and happens again."
                : "No active alerts. Resource thresholds and telemetry collection are being monitored locally.";
        elements.alertsList.appendChild(
            createElement("div", "alert-empty", message)
        );
        return;
    }

    visibleAlerts.forEach(item => {
        const severity = ["warning", "critical"].includes(item.severity)
            ? item.severity
            : "warning";
        const card = createElement("article", `alert-card is-${severity}`);
        const content = createElement("div", "alert-content");
        content.appendChild(createElement("strong", "alert-title", item.title));
        content.appendChild(createElement("p", "alert-message", item.message));
        const badge = createElement("span", "alert-severity", severity);
        const acknowledge = createElement(
            "button",
            "alert-acknowledge",
            "Acknowledge"
        );
        acknowledge.type = "button";
        acknowledge.addEventListener("click", () => {
            acknowledgedAlerts.add(item.id);
            saveAcknowledgedAlerts();
            renderAlerts();
        });
        card.append(badge, content, acknowledge);
        elements.alertsList.appendChild(card);
    });
}


// ========================================
// OPT-IN OPERATIONS CONSOLE
// ========================================

function appendConsoleOutput(title, value, isError = false) {
    if (!elements.consoleOutput) return;

    const entry = createElement(
        "article",
        "console-entry" + (isError ? " is-error" : "")
    );
    entry.appendChild(
        createElement(
            "div",
            "console-entry-heading",
            `${new Date().toLocaleTimeString()}  ${title}`
        )
    );
    entry.appendChild(
        createElement("pre", "console-entry-result", value)
    );
    elements.consoleOutput.prepend(entry);
}

async function loadConsoleActions() {
    if (consoleActionsLoaded || !elements.consoleActions) return;

    try {
        const data = await apiGet("/api/console/actions");
        elements.consoleActions.replaceChildren();
        (data.actions || []).forEach(action => {
            const button = createElement(
                "button",
                "console-action",
                action.label
            );
            button.type = "button";
            button.dataset.action = action.id;
            elements.consoleActions.appendChild(button);
        });
        consoleActionsLoaded = true;
    } catch (error) {
        appendConsoleOutput("Console unavailable", error.message, true);
    }
}

if (elements.consoleToggle && elements.consolePanel) {
    elements.consoleToggle.disabled = !accessPermissions.monitoring;
    if (!accessPermissions.monitoring) {
        elements.consoleToggle.title = "Grant system monitoring access first.";
    }

    elements.consoleToggle.addEventListener("click", async () => {
        const enabled = elements.consolePanel.hidden;
        elements.consolePanel.hidden = !enabled;
        elements.consoleToggle.setAttribute("aria-expanded", String(enabled));
        setText(
            elements.consoleToggle,
            enabled ? "Disable console" : "Enable console"
        );
        if (enabled) await loadConsoleActions();
    });
}

if (elements.consoleActions) {
    elements.consoleActions.addEventListener("click", async event => {
        const button = event.target.closest("button[data-action]");
        if (!button || !elements.consolePanel || elements.consolePanel.hidden) {
            return;
        }

        button.disabled = true;
        try {
            const result = await apiGet(
                "/api/console/" + encodeURIComponent(button.dataset.action)
            );
            appendConsoleOutput(
                result.label,
                JSON.stringify(result.result, null, 2)
            );
        } catch (error) {
            appendConsoleOutput("Diagnostic failed", error.message, true);
        } finally {
            button.disabled = false;
        }
    });
}


// ========================================
// LIVE PERFORMANCE CHARTS
// ========================================

const CHART_COLORS = {
    cpu: "#6d98ff",
    memory: "#68c58d",
    disk: "#d3aa62",
    rx: "#6d98ff",
    tx: "#68c58d"
};

function metricNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}

function recordMetricsSample(metrics) {
    const timestamp = Date.now();
    const monotonicTime = performance.now();
    const network = metrics.network || {};
    const received = metricNumber(network.bytes_received);
    const sent = metricNumber(network.bytes_sent);
    let rx = null;
    let tx = null;

    if (previousNetworkCounters) {
        const elapsed = (monotonicTime - previousNetworkCounters.time) / 1000;
        if (elapsed > 0) {
            if (
                received !== null &&
                received >= previousNetworkCounters.received
            ) {
                rx = ((received - previousNetworkCounters.received) * 8) / elapsed;
            }
            if (sent !== null && sent >= previousNetworkCounters.sent) {
                tx = ((sent - previousNetworkCounters.sent) * 8) / elapsed;
            }
        }
    }

    if (received !== null && sent !== null) {
        previousNetworkCounters = {
            received,
            sent,
            time: monotonicTime
        };
    }

    chartSamples.push({
        timestamp,
        sampledAt: monotonicTime,
        cpu: metricNumber(metrics.cpu_usage),
        memory: metricNumber(metrics.memory_usage),
        disk: metricNumber(metrics.disk_usage),
        rx,
        tx
    });

    const oldestSample = timestamp - MAX_CHART_HISTORY_SECONDS * 1000;
    chartSamples = chartSamples.filter(
        sample => sample.timestamp >= oldestSample
    );
    renderPerformanceCharts();
}

function formatBitRate(value) {
    if (value >= 1_000_000) {
        return (value / 1_000_000).toFixed(2) + " Mbps";
    }
    if (value >= 1_000) {
        return (value / 1_000).toFixed(2) + " Kbps";
    }
    return value.toFixed(0) + " bps";
}

function formatChartTime(timestamp) {
    return new Date(timestamp).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit"
    });
}

function chartStatistics(values) {
    const valid = values.filter(Number.isFinite);
    if (!valid.length) return null;
    return {
        current: valid[valid.length - 1],
        average: valid.reduce((total, value) => total + value, 0) / valid.length,
        peak: Math.max(...valid)
    };
}

function renderChartStats(container, series, formatter) {
    if (!container) return;
    container.replaceChildren();

    series.forEach(item => {
        const stats = chartStatistics(item.values);
        const card = createElement("div", "chart-stat");
        card.style.borderLeftColor = item.color;
        const header = createElement("div", "chart-stat-heading");
        header.appendChild(createElement("span", "", item.label));
        header.appendChild(
            createElement(
                "strong",
                "",
                stats ? formatter(stats.current) : "—"
            )
        );
        card.appendChild(header);
        card.appendChild(
            createElement(
                "small",
                "",
                stats
                    ? `AVG ${formatter(stats.average)}  ·  PEAK ${formatter(stats.peak)}`
                    : "Waiting for samples"
            )
        );
        container.appendChild(card);
    });
}

function niceAxisMaximum(value) {
    if (!Number.isFinite(value) || value <= 0) return 1;
    const magnitude = 10 ** Math.floor(Math.log10(value));
    const fraction = value / magnitude;
    const step = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
    return step * magnitude;
}

function downsampleChartSamples(samples, series, limit = 420) {
    if (samples.length <= limit) return samples;

    const bucketCount = Math.max(
        1,
        Math.floor(limit / (series.length * 2))
    );
    const bucketWidth = samples.length / bucketCount;
    const selected = new Set([0, samples.length - 1]);

    for (let bucket = 0; bucket < bucketCount; bucket += 1) {
        const start = Math.floor(bucket * bucketWidth);
        const end = Math.min(
            samples.length,
            Math.floor((bucket + 1) * bucketWidth)
        );
        series.forEach(item => {
            let minimumIndex = -1;
            let maximumIndex = -1;
            for (let index = start; index < end; index += 1) {
                const value = samples[index][item.key];
                if (!Number.isFinite(value)) continue;
                if (
                    minimumIndex === -1 ||
                    value < samples[minimumIndex][item.key]
                ) {
                    minimumIndex = index;
                }
                if (
                    maximumIndex === -1 ||
                    value > samples[maximumIndex][item.key]
                ) {
                    maximumIndex = index;
                }
            }
            if (minimumIndex !== -1) selected.add(minimumIndex);
            if (maximumIndex !== -1) selected.add(maximumIndex);
        });
    }

    return [...selected]
        .sort((left, right) => left - right)
        .map(index => samples[index]);
}

function smoothChartSamples(samples, series, timeConstant = 900) {
    const previousValues = new Map();
    let previousTime = null;

    return samples.map(sample => {
        const smoothed = { ...sample };
        const elapsed = previousTime === null
            ? 0
            : Math.max(0, sample.sampledAt - previousTime);
        const alpha = elapsed
            ? 1 - Math.exp(-elapsed / timeConstant)
            : 1;

        series.forEach(item => {
            const rawValue = sample[item.key];
            if (!Number.isFinite(rawValue)) {
                smoothed[item.key] = rawValue;
                return;
            }

            const previousValue = previousValues.get(item.key);
            const value = Number.isFinite(previousValue)
                ? previousValue + (rawValue - previousValue) * alpha
                : rawValue;
            smoothed[item.key] = value;
            previousValues.set(item.key, value);
        });

        previousTime = sample.sampledAt;
        return smoothed;
    });
}

function createChartFrameData(samples, series, options) {
    const smoothedSamples = smoothChartSamples(samples, series);
    return {
        samples: downsampleChartSamples(smoothedSamples, series),
        previousSample: smoothedSamples.length > 1
            ? smoothedSamples[smoothedSamples.length - 2]
            : null,
        series,
        ...options
    };
}

function drawSmoothCurve(context, points) {
    if (!points.length) return;
    context.moveTo(points[0].x, points[0].y);
    for (let index = 0; index < points.length - 1; index += 1) {
        const previous = points[Math.max(0, index - 1)];
        const current = points[index];
        const next = points[index + 1];
        const afterNext = points[Math.min(points.length - 1, index + 2)];
        const lowY = Math.min(current.y, next.y);
        const highY = Math.max(current.y, next.y);
        const firstControlY = Math.min(
            Math.max(
                current.y + (next.y - previous.y) / 6,
                lowY
            ),
            highY
        );
        const secondControlY = Math.min(
            Math.max(
                next.y - (afterNext.y - current.y) / 6,
                lowY
            ),
            highY
        );
        context.bezierCurveTo(
            current.x + (next.x - previous.x) / 6,
            firstControlY,
            next.x - (afterNext.x - current.x) / 6,
            secondControlY,
            next.x,
            next.y
        );
    }
}

let chartFontFamily = null;

function drawCanvasChart(container, dataset, now, monotonicNow) {
    if (!container) return;

    if (!dataset || dataset.samples.length < 2) {
        if (!container.querySelector(".chart-empty")) {
            container.replaceChildren(
                createElement(
                    "div",
                    "chart-empty",
                    dataset?.emptyMessage || "Collecting the next sample..."
                )
            );
        }
        return;
    }

    let canvas = container.querySelector("canvas");
    if (!canvas) {
        canvas = createElement("canvas", "chart-plot");
        canvas.setAttribute("role", "img");
        container.replaceChildren(canvas);
    }
    canvas.setAttribute("aria-label", dataset.label);

    const width = Math.max(320, Math.round(container.clientWidth || 920));
    const height = 280;
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    const pixelWidth = Math.round(width * pixelRatio);
    const pixelHeight = Math.round(height * pixelRatio);
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
        canvas.width = pixelWidth;
        canvas.height = pixelHeight;
    }

    const context = canvas.getContext("2d");
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    context.clearRect(0, 0, width, height);
    if (!chartFontFamily) {
        chartFontFamily = getComputedStyle(document.body).fontFamily;
    }

    const margin = {
        top: 16,
        right: 16,
        bottom: 36,
        left: width < 500 ? 54 : 66
    };
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const bottom = margin.top + plotHeight;
    const availableRange = now - dataset.samples[0].timestamp;
    const timeRange = Math.min(
        selectedChartRange * 1000,
        Math.max(1000, availableRange)
    );
    const startTime = now - timeRange;
    const maximum = dataset.maximum;

    context.font = `500 11px ${chartFontFamily}`;
    context.fillStyle = "#aab4be";
    context.textBaseline = "middle";
    context.lineWidth = 1;
    context.setLineDash([]);

    for (let tick = 0; tick <= 4; tick += 1) {
        const ratio = tick / 4;
        const y = margin.top + ratio * plotHeight;
        context.strokeStyle = "#2a3036";
        context.beginPath();
        context.moveTo(margin.left, y);
        context.lineTo(width - margin.right, y);
        context.stroke();
        context.textAlign = "right";
        context.fillText(
            dataset.formatAxis(maximum * (1 - ratio)),
            margin.left - 10,
            y
        );

        const x = margin.left + ratio * plotWidth;
        context.strokeStyle = "#20262c";
        context.setLineDash([2, 5]);
        context.beginPath();
        context.moveTo(x, margin.top);
        context.lineTo(x, bottom);
        context.stroke();
        context.setLineDash([]);
    }

    const timeLabelKey = `${Math.floor(now / 1000)}:${selectedChartRange}`;
    if (!chartTimeLabelCache || chartTimeLabelCache.key !== timeLabelKey) {
        const ratios = width < 600
            ? [0, 0.5, 1]
            : [0, 0.25, 0.5, 0.75, 1];
        chartTimeLabelCache = {
            key: timeLabelKey,
            labels: ratios.map((ratio, index) => ({
                ratio,
                alignment: index === 0
                    ? "left"
                    : index === ratios.length - 1
                        ? "right"
                        : "center",
                text: formatChartTime(startTime + ratio * timeRange)
            }))
        };
    }
    context.textBaseline = "alphabetic";
    chartTimeLabelCache.labels.forEach(label => {
        context.textAlign = label.alignment;
        context.fillText(
            label.text,
            margin.left + label.ratio * plotWidth,
            height - 9
        );
    });

    const latestSample = dataset.samples[dataset.samples.length - 1];
    const previousSample = dataset.previousSample;
    const sampleDuration = previousSample && latestSample.sampledAt
        ? Math.max(
            1,
            latestSample.sampledAt - previousSample.sampledAt
        )
        : REFRESH_INTERVAL;
    const progress = previousSample && latestSample.sampledAt
        ? Math.min(
            1,
            Math.max(
                0,
                (monotonicNow - latestSample.sampledAt) / sampleDuration
            )
        )
        : 1;

    context.save();
    context.beginPath();
    context.rect(margin.left, margin.top, plotWidth, plotHeight);
    context.clip();

    dataset.series.forEach(item => {
        const points = [];
        dataset.samples.forEach((sample, index) => {
            let value = sample[item.key];
            let timestamp = sample.timestamp;
            if (index === dataset.samples.length - 1) {
                const previousValue = previousSample?.[item.key];
                const currentValue = latestSample[item.key];
                if (
                    Number.isFinite(previousValue) &&
                    Number.isFinite(currentValue)
                ) {
                    value = previousValue +
                        (currentValue - previousValue) * progress;
                }
                timestamp = now;
            }
            if (!Number.isFinite(value)) return;
            const ratio = (timestamp - startTime) / timeRange;
            points.push({
                x: margin.left + ratio * plotWidth,
                y: margin.top + (
                    1 - Math.min(maximum, Math.max(0, value)) / maximum
                ) * plotHeight
            });
        });
        if (!points.length) return;

        if (item.fill && points.length > 1) {
            const gradient = context.createLinearGradient(
                0,
                margin.top,
                0,
                bottom
            );
            gradient.addColorStop(0, `${item.color}4d`);
            gradient.addColorStop(1, `${item.color}03`);
            context.beginPath();
            context.moveTo(points[0].x, bottom);
            context.lineTo(points[0].x, points[0].y);
            drawSmoothCurve(context, points);
            context.lineTo(points[points.length - 1].x, bottom);
            context.closePath();
            context.fillStyle = gradient;
            context.fill();
        }

        context.beginPath();
        drawSmoothCurve(context, points);
        context.strokeStyle = item.color;
        context.lineWidth = 2.4;
        context.lineCap = "round";
        context.lineJoin = "round";
        context.shadowColor = item.color;
        context.shadowBlur = 7;
        context.stroke();
        context.shadowBlur = 0;

        const latestPoint = points[points.length - 1];
        context.beginPath();
        context.arc(latestPoint.x, latestPoint.y, 3.2, 0, Math.PI * 2);
        context.fillStyle = item.color;
        context.shadowColor = item.color;
        context.shadowBlur = 9;
        context.fill();
        context.shadowBlur = 0;
    });

    context.restore();
}

function drawPerformanceCharts(now = Date.now(), monotonicNow = performance.now()) {
    drawCanvasChart(
        elements.resourceChart,
        chartFrameData.resource,
        now,
        monotonicNow
    );
    drawCanvasChart(
        elements.networkChart,
        chartFrameData.network,
        now,
        monotonicNow
    );
}

function chartSectionIsVisible() {
    if (document.visibilityState !== "visible") return false;
    const section = document.getElementById("charts");
    if (!section) return false;
    const bounds = section.getBoundingClientRect();
    return bounds.bottom > 0 && bounds.top < window.innerHeight;
}

function animatePerformanceCharts() {
    chartAnimationFrame = null;
    if (!chartSectionIsVisible()) return;

    drawPerformanceCharts();
    chartAnimationFrame = requestAnimationFrame(animatePerformanceCharts);
}

function scheduleChartAnimation() {
    if (chartAnimationFrame === null) {
        chartAnimationFrame = requestAnimationFrame(animatePerformanceCharts);
    }
}

function renderPerformanceCharts() {
    const cutoff = Date.now() - selectedChartRange * 1000;
    const samples = chartSamples.filter(sample => sample.timestamp >= cutoff);
    const resourceSeries = [
        { key: "cpu", label: "CPU", color: CHART_COLORS.cpu },
        { key: "memory", label: "Memory", color: CHART_COLORS.memory },
        { key: "disk", label: "Disk", color: CHART_COLORS.disk }
    ];
    const resourceStats = resourceSeries.map(item => ({
        ...item,
        values: samples.map(sample => sample[item.key])
    }));

    renderChartStats(
        elements.resourceChartStats,
        resourceStats,
        value => value.toFixed(1) + "%"
    );
    chartFrameData.resource = createChartFrameData(
        samples,
        resourceSeries,
        {
            maximum: 100,
            formatAxis: value => Math.round(value) + "%",
            label: `CPU, memory, and disk utilisation over the last ${selectedChartRange} seconds`,
            emptyMessage: "Collecting the next sample..."
        }
    );

    const networkSeries = [
        { key: "rx", label: "RX / In", color: CHART_COLORS.rx, fill: true },
        { key: "tx", label: "TX / Out", color: CHART_COLORS.tx, fill: true }
    ];
    const networkStats = networkSeries.map(item => ({
        ...item,
        values: samples.map(sample => sample[item.key])
    }));
    const networkValues = networkStats.flatMap(item =>
        item.values.filter(Number.isFinite)
    );
    const networkMaximum = niceAxisMaximum(
        Math.max(1_000, ...networkValues)
    );

    renderChartStats(
        elements.networkChartStats,
        networkStats,
        formatBitRate
    );
    chartFrameData.network = createChartFrameData(
        samples,
        networkSeries,
        {
            maximum: networkMaximum,
            formatAxis: formatBitRate,
            label: `Aggregate inbound and outbound throughput over the last ${selectedChartRange} seconds`,
            emptyMessage: "Waiting for a second sample to calculate network throughput..."
        }
    );
    if (chartSectionIsVisible()) {
        drawPerformanceCharts();
    }
    scheduleChartAnimation();
}

document.querySelectorAll("[data-chart-range]").forEach(button => {
    button.addEventListener("click", () => {
        const range = Number(button.dataset.chartRange);
        if (![60, 300, 900].includes(range)) return;
        selectedChartRange = range;
        document.querySelectorAll("[data-chart-range]").forEach(option => {
            const selected = option === button;
            option.classList.toggle("is-active", selected);
            option.setAttribute("aria-pressed", String(selected));
        });
        renderPerformanceCharts();
    });
});

window.addEventListener("resize", renderPerformanceCharts);
window.addEventListener("scroll", scheduleChartAnimation, { passive: true });
document.addEventListener("visibilitychange", scheduleChartAnimation);


// ========================================
// SYSTEM METRICS
// ========================================

async function updateDashboard() {
    if (dashboardRequestInFlight) return;
    dashboardRequestInFlight = true;

    try {
        const metrics = await apiGet("/api/metrics");
        setAlertSource("metrics", metrics.alerts || []);
        recordMetricsSample(metrics);

        setText(
            elements.cpu,
            Number(metrics.cpu_usage).toFixed(1)
        );
        setProgressBar(elements.cpuBar, metrics.cpu_usage);

        setText(
            elements.memory,
            Number(metrics.memory_usage).toFixed(1)
        );
        setProgressBar(elements.memoryBar, metrics.memory_usage);

        setText(
            elements.memoryTotal,
            formatGB(metrics.memory_total) + " total"
        );

        setText(
            elements.disk,
            Number(metrics.disk_usage).toFixed(1)
        );
        setProgressBar(elements.diskBar, metrics.disk_usage);

        setText(
            elements.diskTotal,
            formatGB(metrics.disk_total) + " total"
        );

        setText(
            elements.uptime,
            metrics.uptime?.formatted || "Unavailable"
        );

        setConnectionStatus(true);
        updateTimestamp();

    } catch (error) {
        console.error("CloudOps metrics error:", error);
        setAlertSource("metrics", [{
            id: "metrics-unavailable",
            severity: "critical",
            title: "Metrics collection failed",
            message: "Local system metrics could not be collected. Check the application logs and refresh.",
        }]);
        setConnectionStatus(false);
    } finally {
        dashboardRequestInFlight = false;
    }
}


// ========================================
// STORAGE METRICS
// ========================================

async function updateStorageMetrics() {
    try {
        const data = await apiGet("/api/storage");
        setAlertSource("storage", []);
        const summary = data.summary || {};
        const devices = data.devices || [];

        setText(elements.storageTotal, formatGB(summary.total_gb));
        setText(elements.storageUsed, formatGB(summary.used_gb));
        setText(elements.storageFree, formatGB(summary.free_gb));

        setText(
            elements.storagePercent,
            Number(summary.usage_percent).toFixed(1) + "% utilisation"
        );

        setText(elements.storageDeviceCount, String(devices.length));

        setText(
            elements.storageDeviceStatus,
            devices.length ? "DEVICES ONLINE" : "NO DEVICES FOUND"
        );

        renderStorageDevices(devices);

    } catch (error) {
        console.error("CloudOps storage error:", error);
        setAlertSource("storage", [{
            id: "storage-unavailable",
            severity: "critical",
            title: "Storage collection failed",
            message: "Local storage metrics could not be collected. Check the application logs and refresh.",
        }]);
        setText(elements.storageDeviceStatus, "STORAGE UNAVAILABLE");
    }
}

function renderStorageDevices(devices) {
    if (!elements.storageDevices) return;

    elements.storageDevices.replaceChildren();

    if (!devices.length) {
        elements.storageDevices.appendChild(
            createElement("div", "storage-empty", "No storage devices found.")
        );
        return;
    }

    devices.forEach(device => {
        const item = createElement("div", "storage-device");
        const top = createElement("div", "storage-device-top");
        const identity = createElement("div", "storage-device-identity");

        const icon = createElement("div", "storage-device-icon", "▤");
        const name = createElement("div", "storage-device-name");

        name.appendChild(
            createElement("strong", "", device.device || "Unknown device")
        );

        name.appendChild(
            createElement(
                "small",
                "",
                `${device.filesystem || "Unknown"} · ${device.mountpoint || "Unknown mount"}`
            )
        );

        identity.append(icon, name);

        const usage = Number(device.usage_percent) || 0;

        const level = usage >= 90
            ? "is-critical"
            : usage >= 75
                ? "is-warning"
                : "is-healthy";

        top.append(
            identity,
            createElement(
                "div",
                "storage-device-usage " + level,
                usage.toFixed(1) + "%"
            )
        );

        const progress = createElement("div", "storage-device-progress");
        const bar = createElement("div", "storage-device-progress-bar " + level);

        bar.style.width = Math.min(100, Math.max(0, usage)) + "%";
        progress.appendChild(bar);

        const details = createElement("div", "storage-device-details");

        details.append(
            createElement("span", "", "Used: " + formatGB(device.used_gb)),
            createElement("span", "", "Free: " + formatGB(device.free_gb)),
            createElement("span", "", "Total: " + formatGB(device.total_gb))
        );

        item.append(top, progress, details);
        elements.storageDevices.appendChild(item);
    });
}


// ========================================
// STORAGE DIRECTORY BROWSER
// ========================================

function renderStorageBreadcrumbs(path) {
    if (!elements.browserBreadcrumbs) return;
    renderPathBreadcrumbs(
        elements.browserBreadcrumbs,
        path,
        browseStorage
    );
}

function renderStorageDirectory(entries) {
    if (!elements.browserEntries) return;

    elements.browserEntries.replaceChildren();

    if (!entries.length) {
        elements.browserEntries.appendChild(
            createElement("div", "storage-empty", "This directory is empty.")
        );
        return;
    }

    entries.forEach(entry => {
        const isDirectory = entry.type === "directory";
        const row = createElement("div", "browser-entry");
        const name = createElement("div", "browser-entry-name");

        const icon = createElement(
            "span",
            "",
            entryIcon(entry)
        );

        const button = createElement("button", "", entry.name);
        button.type = "button";
        button.disabled = !isDirectory;

        if (isDirectory) {
            button.addEventListener("click", () => {
                browseStorage(joinPath(storagePath, entry.name));
            });
        }

        name.append(icon, button);

        row.append(
            name,
            createElement("div", "browser-entry-type", entry.type),
            createElement(
                "div",
                "browser-entry-size",
                isDirectory ? "—" : formatBytes(entry.size_bytes)
            ),
            createElement(
                "div",
                "browser-entry-modified",
                formatDate(entry.modified)
            )
        );

        elements.browserEntries.appendChild(row);
    });
}

async function browseStorage(path = storagePath) {
    if (!elements.browserEntries) return;

    const requestNumber = ++storageRequestNumber;

    elements.browserEntries.replaceChildren(
        createElement("div", "storage-empty", "Loading directory...")
    );

    try {
        const data = await apiGet("/api/storage/browse", {
            root: ROOT_ID,
            path
        });

        if (requestNumber !== storageRequestNumber) return;

        storagePath = data.path || ROOT_PATH;
        const entries = data.entries || [];

        renderStorageBreadcrumbs(storagePath);
        renderStorageDirectory(entries);

        setText(
            elements.browserEntryCount,
            entries.length + " ITEMS"
        );

        if (elements.browserBack) {
            elements.browserBack.disabled = isRootPath(storagePath);
        }

    } catch (error) {
        if (requestNumber !== storageRequestNumber) return;

        console.error("CloudOps storage browser error:", error);

        elements.browserEntries.replaceChildren(
            createElement("div", "storage-empty", error.message)
        );

        setText(elements.browserEntryCount, "ERROR");
    }
}

if (elements.browserBack) {
    elements.browserBack.addEventListener("click", () => {
        browseStorage(parentPath(storagePath));
    });
}

if (elements.storageRefresh) {
    elements.storageRefresh.addEventListener("click", async () => {
        elements.storageRefresh.disabled = true;

        try {
            const updates = [];
            if (accessPermissions.monitoring) {
                updates.push(updateStorageMetrics());
            }
            if (accessPermissions.read_files) {
                updates.push(browseStorage(storagePath));
            }
            await Promise.all(updates);
        } finally {
            elements.storageRefresh.disabled = false;
        }
    });
}


// ========================================
// FILE MANAGER: BREADCRUMBS
// ========================================

function renderFileManagerBreadcrumbs(path) {
    if (!elements.fmBreadcrumbs) return;
    renderPathBreadcrumbs(
        elements.fmBreadcrumbs,
        path,
        browseFileManager
    );
}


// ========================================
// FILE MANAGER: DIRECTORY LISTING
// ========================================

function renderFileManagerEntries(entries) {
    if (!elements.fmEntries) return;

    elements.fmEntries.replaceChildren();

    const search = (elements.fmSearch?.value || "").toLowerCase();

    const filtered = entries.filter(entry =>
        entry.name.toLowerCase().includes(search)
    );

    setText(
        elements.fmCount,
        `${filtered.length} of ${entries.length} items`
    );

    if (!filtered.length) {
        elements.fmEntries.appendChild(
            createElement(
                "div",
                "fm-empty",
                search ? "No matching files or folders." : "This directory is empty."
            )
        );
        return;
    }

    filtered.forEach(entry => {
        const row = createElement("div", "fm-entry");
        const name = createElement("div", "fm-entry-name");

        const icon = createElement("span", "fm-entry-icon", entryIcon(entry));
        const openButton = createElement("button", "", entry.name);

        openButton.type = "button";

        if (entry.type === "directory") {
            openButton.addEventListener("click", () => {
                browseFileManager(joinPath(fileManagerPath, entry.name));
            });
        } else if (entry.type === "file") {
            openButton.addEventListener("click", () => {
                openFile(entry);
            });
        } else {
            openButton.disabled = true;
            openButton.title = "Symlinks and special files are not opened.";
        }

        name.append(icon, openButton);

        const actions = createElement("div", "fm-entry-actions");

        addAction(actions, "Info", () => showProperties(entry.path));

        if (entry.type === "file") {
            addAction(actions, "Download", () => downloadFile(entry.path));
        }

        if (entry.type !== "symlink" && entry.type !== "other") {
            if (accessPermissions.write_files) {
                addAction(actions, "Rename", () => renameEntry(entry));
                addAction(actions, "Copy", () => copyEntry(entry));
                addAction(actions, "Move", () => moveEntry(entry));
                addAction(
                    actions,
                    "Delete",
                    () => deleteEntry(entry),
                    true
                );
            }
        }

        row.append(
            name,
            createElement("div", "fm-entry-type", entry.type),
            createElement(
                "div",
                "fm-entry-size",
                entry.type === "directory" ? "—" : formatBytes(entry.size_bytes)
            ),
            createElement(
                "div",
                "fm-entry-modified",
                formatDate(entry.modified)
            ),
            actions
        );

        elements.fmEntries.appendChild(row);
    });
}

function addAction(container, label, callback, danger = false) {
    const button = createElement(
        "button",
        "fm-action" + (danger ? " fm-danger" : ""),
        label
    );

    button.type = "button";
    button.addEventListener("click", callback);

    container.appendChild(button);
}


// ========================================
// FILE MANAGER: BROWSE
// ========================================

async function browseFileManager(path = fileManagerPath) {
    if (!elements.fmEntries) return;

    const previousPath = fileManagerPath;
    const previousScrollY = window.scrollY;
    const shouldRestoreScroll = path !== previousPath;
    const requestNumber = ++fileManagerRequestNumber;

    elements.fmEntries.replaceChildren(
        createElement("div", "fm-empty", "Loading filesystem...")
    );

    try {
        const data = await apiGet("/api/storage/browse", {
            root: ROOT_ID,
            path
        });

        if (requestNumber !== fileManagerRequestNumber) return;

        fileManagerPath = data.path || ROOT_PATH;
        fileManagerEntries = data.entries || [];

        renderFileManagerBreadcrumbs(fileManagerPath);
        renderFileManagerEntries(fileManagerEntries);

        setText(elements.fmCurrentPath, fileManagerPath);

        if (elements.fmBack) {
            elements.fmBack.disabled = isRootPath(fileManagerPath);
        }

        if (elements.fmUp) {
            elements.fmUp.disabled = isRootPath(fileManagerPath);
        }

        if (shouldRestoreScroll) {
            requestAnimationFrame(() => window.scrollTo(0, previousScrollY));
        }

    } catch (error) {
        if (requestNumber !== fileManagerRequestNumber) return;

        console.error("File Manager browse error:", error);

        elements.fmEntries.replaceChildren(
            createElement("div", "fm-empty", error.message)
        );

        setText(elements.fmCount, "Unable to load directory");
    }
}

function refreshFileManager() {
    return browseFileManager(fileManagerPath);
}


// ========================================
// FILE MANAGER: OPEN FILES
// ========================================

async function openFile(entry) {
    if (entry.type !== "file") return;

    const path = entry.path;

    if (isImageFile(entry.name) || isPdfFile(entry.name)) {
        await previewInlineFile(path, entry.name);
        return;
    }

    if (isDocxFile(entry.name)) {
        await previewDocxFile(path, entry.name);
        return;
    }

    await previewTextFile(path, entry.name);
}

async function openTextEditor(path) {
    try {
        const data = await apiGet("/api/files/read", {
            root: ROOT_ID,
            path
        });

        editingPath = data.path;

        setText(elements.editorTitle, data.path);
        setText(
            elements.editorInfo,
            `${data.mime_type || "Text"} · ${formatBytes(data.size_bytes)} · UTF-8 · Maximum 1 MiB`
        );

        elements.editorContent.value = data.content;
        setText(elements.editorStatus, "Loaded successfully");

        elements.editorPanel.hidden = false;
        elements.previewPanel.hidden = true;
        elements.propertiesPanel.hidden = true;

        elements.editorPanel.scrollIntoView({
            behavior: "smooth",
            block: "start"
        });

    } catch (error) {
        showError(error, "File editor");
    }
}

async function saveEditor() {
    if (!editingPath) return;

    if (!confirm("Save changes to this file?")) {
        return;
    }

    elements.editorSave.disabled = true;
    setText(elements.editorStatus, "Saving...");

    try {
        await apiPost("/api/files/save", {
            root: ROOT_ID,
            path: editingPath,
            content: elements.editorContent.value
        });

        setText(elements.editorStatus, "Saved successfully");
        await refreshFileManager();

    } catch (error) {
        setText(elements.editorStatus, "Save failed: " + error.message);
        showError(error, "Save file");

    } finally {
        elements.editorSave.disabled = false;
    }
}

function closeEditor() {
    elements.editorPanel.hidden = true;
    editingPath = null;
}


// ========================================
// FILE MANAGER: IN-APP PREVIEW
// ========================================

function beginFilePreview(path, name) {
    previewFilePath = path;
    setText(elements.previewTitle, name);
    setText(elements.previewInfo, path);
    elements.previewImage.removeAttribute("src");
    elements.previewFrame.removeAttribute("src");
    elements.previewDocumentContent.replaceChildren();
    elements.previewImageStage.hidden = true;
    elements.previewFrame.hidden = true;
    elements.previewDocumentStage.hidden = false;
    elements.previewDocumentStage.replaceChildren(
        elements.previewDocumentContent
    );
    elements.previewEdit.hidden = true;
    elements.previewPanel.hidden = false;
    elements.editorPanel.hidden = true;
    elements.propertiesPanel.hidden = true;
}

async function previewInlineFile(path, name) {
    try {
        const url = apiUrl("/api/files/preview", {
            root: ROOT_ID,
            path
        });

        const response = await fetch(url, {
            method: "HEAD",
            cache: "no-store"
        });

        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(data.error || "Unable to preview this file.");
        }

        if (!isPdfFile(name)) {
            previewFilePath = path;
            setText(elements.imageDialogTitle, name);
            setText(elements.imageDialogPath, path);
            elements.imageDialogImage.src = url.toString();
            elements.imageDialog.showModal();
            elements.imageDialogClose.focus();
            return;
        }

        beginFilePreview(path, name);
        if (isPdfFile(name)) {
            elements.previewFrame.src = url.toString();
            elements.previewFrame.hidden = false;
            setText(elements.previewInfo, `${path} · PDF`);
        }

        elements.previewPanel.scrollIntoView({
            behavior: "smooth",
            block: "start"
        });

    } catch (error) {
        showFilePreviewMessage(path, name, error.message);
    }
}

async function previewTextFile(path, name) {
    try {
        const data = await apiGet("/api/files/read", {
            root: ROOT_ID,
            path
        });

        beginFilePreview(path, name);
        const content = createElement("pre", "fm-code-preview");
        content.textContent = data.content;
        elements.previewDocumentContent.appendChild(content);
        elements.previewEdit.hidden = !accessPermissions.write_files;
        elements.previewInfo.textContent =
            `${formatBytes(data.size_bytes)} · UTF-8 text · Read-only preview`;
        elements.previewPanel.scrollIntoView({
            behavior: "smooth",
            block: "start"
        });
    } catch (error) {
        showFilePreviewMessage(path, name, error.message);
    }
}

async function previewDocxFile(path, name) {
    try {
        const data = await apiGet("/api/files/docx-preview", {
            root: ROOT_ID,
            path
        });

        beginFilePreview(path, name);
        (data.blocks || []).forEach(block => {
            if (block.type === "paragraph") {
                const heading = /^Heading([1-6])$/i.exec(block.style || "");
                const tag = /^Title$/i.test(block.style || "")
                    ? "h2"
                    : heading
                        ? Number(heading[1]) <= 2 ? "h3" : "h4"
                        : "p";
                const paragraph = createElement(
                    tag,
                    heading ? `docx-heading level-${heading[1]}` : "docx-paragraph",
                    block.text
                );
                elements.previewDocumentContent.appendChild(paragraph);
            } else if (block.type === "table") {
                const table = createElement("table", "docx-table");
                const body = createElement("tbody");
                (block.rows || []).forEach(row => {
                    const tableRow = createElement("tr");
                    (row || []).forEach(value => {
                        tableRow.appendChild(
                            createElement("td", "", value)
                        );
                    });
                    body.appendChild(tableRow);
                });
                table.appendChild(body);
                elements.previewDocumentContent.appendChild(table);
            }
        });
        if (!elements.previewDocumentContent.childElementCount) {
            elements.previewDocumentContent.appendChild(
                createElement("p", "fm-preview-message", "This document contains no readable text.")
            );
        }
        setText(
            elements.previewInfo,
            `${formatBytes(data.size_bytes)} · Word document · Text and tables`
        );
        elements.previewPanel.scrollIntoView({
            behavior: "smooth",
            block: "start"
        });
    } catch (error) {
        showFilePreviewMessage(path, name, error.message);
    }
}

function showFilePreviewMessage(path, name, message) {
    beginFilePreview(path, name);
    elements.previewDocumentContent.appendChild(
        createElement("p", "fm-preview-error", message)
    );
    setText(
        elements.previewInfo,
        "Preview unavailable · The file can still be downloaded if your current account has access."
    );
    elements.previewPanel.scrollIntoView({
        behavior: "smooth",
        block: "start"
    });
}

function closePreview() {
    elements.previewImage.removeAttribute("src");
    elements.previewFrame.removeAttribute("src");
    elements.previewPanel.hidden = true;
    previewFilePath = null;
}

function closeImageDialog() {
    if (!elements.imageDialog?.open) return;
    elements.imageDialog.close();
    elements.imageDialogImage.removeAttribute("src");
    previewFilePath = null;
}


// ========================================
// FILE MANAGER: PERMISSION EDITOR
// ========================================

function getInitialPermissionMode(data) {
    let value = data.mode_octal ?? data.permissions_octal ?? data.mode;

    if (value === undefined || value === null) {
        const permissions = String(data.permissions || "");
        const match = permissions.match(/^([rwx-]{9})$/);

        if (match) {
            value = 0;

            const bits = [
                [256, "r"], [128, "w"], [64, "x"],
                [32, "r"], [16, "w"], [8, "x"],
                [4, "r"], [2, "w"], [1, "x"]
            ];

            bits.forEach(([bit, character], index) => {
                if (match[1][index] === character) {
                    value += bit;
                }
            });
        }
    }

    if (typeof value === "string") {
        const cleaned = value.trim().replace(/^0o/, "");

        if (/^[0-7]{3,4}$/.test(cleaned)) {
            value = parseInt(cleaned.slice(-3), 8);
        } else if (/^[0-7]{1,3}$/.test(cleaned)) {
            value = parseInt(cleaned, 8);
        } else {
            value = NaN;
        }
    } else {
        value = Number(value);
    }

    if (!Number.isInteger(value) || value < 0 || value > 511) {
        return null;
    }

    return value;
}

function modeToOctal(mode) {
    return [
        (mode >> 6) & 7,
        (mode >> 3) & 7,
        mode & 7
    ].join("");
}

function createPermissionEditor(data) {
    const wrapper = createElement("section", "fm-permission-editor");

    wrapper.style.cssText = [
        "margin-top:22px",
        "padding:18px",
        "border:1px solid var(--border, #333)",
        "border-radius:0",
        "background:var(--panel, rgba(255,255,255,0.025))"
    ].join(";");

    const heading = createElement("div");
    heading.style.cssText = "margin-bottom:14px";

    const title = createElement("h4", "", "Linux permissions");
    title.style.cssText = "margin:0 0 5px;font-size:15px";

    const description = createElement(
        "p",
        "",
        "Control read, write and execute access for each permission class."
    );
    description.style.cssText =
        "margin:0;font-size:12px;opacity:.7;line-height:1.5";

    heading.append(title, description);

    wrapper.appendChild(heading);

    const mode = getInitialPermissionMode(data);

    if (mode === null) {
        wrapper.appendChild(
            createElement(
                "p",
                "fm-permission-warning",
                "The current permission mode could not be determined. Permission editing is unavailable."
            )
        );
        return wrapper;
    }

    const groups = [
        { name: "Owner", shift: 6 },
        { name: "Group", shift: 3 },
        { name: "Others", shift: 0 }
    ];

    const checkboxes = {};

    const grid = createElement("div");
    grid.style.cssText = [
        "display:grid",
        "grid-template-columns:repeat(auto-fit,minmax(125px,1fr))",
        "gap:12px"
    ].join(";");

    groups.forEach(group => {
        const card = createElement("div");
        card.style.cssText = [
            "padding:12px",
            "border:1px solid var(--border, #333)",
            "border-radius:0"
        ].join(";");

        const groupTitle = createElement("strong", "", group.name);
        groupTitle.style.cssText =
            "display:block;margin-bottom:10px;font-size:13px";

        card.appendChild(groupTitle);

        checkboxes[group.name] = {};

        [
            { label: "Read", bit: 4 },
            { label: "Write", bit: 2 },
            { label: "Execute", bit: 1 }
        ].forEach(permission => {
            const label = createElement("label");
            label.style.cssText = [
                "display:flex",
                "align-items:center",
                "gap:8px",
                "margin:8px 0",
                "font-size:12px",
                "cursor:pointer"
            ].join(";");

            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.checked =
                Boolean(mode & (permission.bit << group.shift));

            checkbox.addEventListener("change", updateModeDisplay);

            label.append(
                checkbox,
                document.createTextNode(permission.label)
            );

            card.appendChild(label);
            checkboxes[group.name][permission.label] = checkbox;
        });

        grid.appendChild(card);
    });

    wrapper.appendChild(grid);

    const modeDisplay = createElement("div");
    modeDisplay.style.cssText = [
        "display:flex",
        "align-items:center",
        "justify-content:space-between",
        "gap:12px",
        "flex-wrap:wrap",
        "margin-top:16px",
        "padding:12px",
        "border-radius:0",
        "background:rgba(255,255,255,0.04)"
    ].join(";");

    const modeLabel = createElement("span", "", "Octal permission");
    modeLabel.style.cssText = "font-size:12px;opacity:.75";

    const modeValue = createElement("strong", "", modeToOctal(mode));
    modeValue.style.cssText =
        "font-family:monospace;font-size:20px;letter-spacing:2px";

    modeDisplay.append(modeLabel, modeValue);
    wrapper.appendChild(modeDisplay);

    const status = createElement("p");
    status.style.cssText =
        "min-height:18px;margin:12px 0 0;font-size:12px;opacity:.8";

    const applyButton = createElement(
        "button",
        "fm-button",
        "Apply permissions"
    );

    applyButton.type = "button";
    applyButton.style.cssText = "margin-top:12px";

    function calculateMode() {
        let result = 0;

        groups.forEach(group => {
            const permissions = checkboxes[group.name];

            if (permissions.Read.checked) {
                result |= 4 << group.shift;
            }

            if (permissions.Write.checked) {
                result |= 2 << group.shift;
            }

            if (permissions.Execute.checked) {
                result |= 1 << group.shift;
            }
        });

        return result;
    }

    function updateModeDisplay() {
        modeValue.textContent = modeToOctal(calculateMode());
        setText(status, "Unsaved permission changes");
        status.style.color = "";
    }

    applyButton.addEventListener("click", async () => {
        const newMode = modeToOctal(calculateMode());

        if (!confirm(
            `Apply permission mode ${newMode} to:\n\n${data.path}\n\nContinue?`
        )) {
            return;
        }

        applyButton.disabled = true;
        setText(status, "Applying permissions...");

        try {
            await apiPost("/api/files/permissions", {
                root: ROOT_ID,
                path: data.path,
                mode: newMode
            });

            setText(status, `Permissions successfully changed to ${newMode}.`);
            status.style.color = "#7dba8f";

            await showProperties(data.path);
            await refreshFileManager();

        } catch (error) {
            setText(status, "Failed: " + error.message);
            status.style.color = "#d17a7a";
            console.error("Permission update error:", error);
        } finally {
            applyButton.disabled = false;
        }
    });

    wrapper.append(status, applyButton);

    return wrapper;
}


// ========================================
// FILE MANAGER: PROPERTIES
// ========================================

async function showProperties(path) {
    try {
        const data = await apiGet("/api/files/properties", {
            root: ROOT_ID,
            path
        });

        elements.propertiesContent.replaceChildren();

        const properties = [
            ["Name", data.name],
            ["Full path", data.path],
            ["Type", data.type],
            ["Size", data.size_bytes == null ? "—" : formatBytes(data.size_bytes)],
            ["Permissions", data.permissions],
            ["Modified", formatDate(data.modified)],
            ["Created / changed", formatDate(data.created)],
            ["Symbolic link", data.is_symlink ? "Yes" : "No"],
            ["Link target", data.link_target || "—"]
        ];
        if (!isWindows) {
            properties.splice(
                5,
                0,
                ["Owner UID", data.owner_uid],
                ["Group GID", data.group_gid]
            );
        }

        properties.forEach(([label, value]) => {
            const item = createElement("div", "fm-property");

            item.append(
                createElement("span", "fm-property-label", label),
                createElement("span", "fm-property-value", value ?? "—")
            );

            elements.propertiesContent.appendChild(item);
        });

        if (
            !data.is_symlink
            && (data.type === "file" || data.type === "directory")
        ) {
            if (isWindows) {
                elements.propertiesContent.appendChild(
                    createElement(
                        "p",
                        "fm-permission-warning",
                        "Windows ACL permissions are managed in File Explorer. CloudOps does not change Windows security descriptors."
                    )
                );
            } else if (accessPermissions.write_files) {
                elements.propertiesContent.appendChild(
                    createPermissionEditor(data)
                );
            }
        }

        elements.propertiesPanel.hidden = false;
        elements.editorPanel.hidden = true;
        elements.previewPanel.hidden = true;

        elements.propertiesPanel.scrollIntoView({
            behavior: "smooth",
            block: "start"
        });

    } catch (error) {
        showError(error, "File properties");
    }
}


// ========================================
// FILE MANAGER: DOWNLOAD
// ========================================

async function downloadFile(path) {
    const url = apiUrl("/api/files/download", {
        root: ROOT_ID,
        path
    });

    try {
        const response = await fetch(url, {
            method: "HEAD",
            cache: "no-store"
        });
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(
                data.error || `Download request failed (${response.status})`
            );
        }

        const link = document.createElement("a");
        link.href = url.toString();
        link.download = path.replace(/\\/g, "/").split("/").pop() || "download";
        link.hidden = true;
        document.body.appendChild(link);
        link.click();
        link.remove();
    } catch (error) {
        showError(error, "Download");
    }
}


// ========================================
// FILE MANAGER: CREATE FILE / FOLDER
// ========================================

async function createNewFile() {
    const name = prompt("Enter the new file name:");

    if (name === null) return;

    if (!name.trim()) {
        alert("A file name is required.");
        return;
    }

    try {
        await apiPost("/api/files/create-file", {
            root: ROOT_ID,
            parent_path: fileManagerPath,
            name: name.trim(),
            content: ""
        });

        await refreshFileManager();

    } catch (error) {
        showError(error, "Create file");
    }
}

async function createNewFolder() {
    const name = prompt("Enter the new folder name:");

    if (name === null) return;

    if (!name.trim()) {
        alert("A folder name is required.");
        return;
    }

    try {
        await apiPost("/api/files/create-folder", {
            root: ROOT_ID,
            parent_path: fileManagerPath,
            name: name.trim()
        });

        await refreshFileManager();

    } catch (error) {
        showError(error, "Create folder");
    }
}


// ========================================
// FILE MANAGER: UPLOAD
// ========================================

async function uploadSelectedFile(file) {
    if (!file) return;

    const formData = new FormData();

    formData.append("root", ROOT_ID);
    formData.append("parent_path", fileManagerPath);
    formData.append("file", file);

    try {
        const response = await fetch("/api/files/upload", {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "X-CSRF-Token": csrfToken
            },
            body: formData
        });

        const data = await response.json().catch(() => ({}));

        if (!response.ok) {
            throw new Error(
                data.error || `Upload failed (${response.status})`
            );
        }

        alert("Upload completed successfully.");
        await refreshFileManager();

    } catch (error) {
        showError(error, "Upload");
    } finally {
        elements.fmUploadInput.value = "";
    }
}


// ========================================
// FILE MANAGER: RENAME
// ========================================

async function renameEntry(entry) {
    const newName = prompt("Enter the new name:", entry.name);

    if (newName === null || newName === entry.name) return;

    if (!newName.trim()) {
        alert("A name is required.");
        return;
    }

    try {
        await apiPost("/api/files/rename", {
            root: ROOT_ID,
            path: entry.path,
            new_name: newName.trim()
        });

        await refreshFileManager();

    } catch (error) {
        showError(error, "Rename");
    }
}


// ========================================
// FILE MANAGER: COPY
// ========================================

async function copyEntry(entry) {
    const suggested = joinPath(fileManagerPath, "copy-" + entry.name);

    const destination = prompt(
        "Enter the complete destination path for the copy:",
        suggested
    );

    if (destination === null) return;

    if (!destination.trim()) {
        alert("A destination path is required.");
        return;
    }

    try {
        await apiPost("/api/files/copy", {
            root: ROOT_ID,
            source_path: entry.path,
            destination_path: destination.trim()
        });

        alert("Copy completed.");
        await refreshFileManager();

    } catch (error) {
        showError(error, "Copy");
    }
}


// ========================================
// FILE MANAGER: MOVE
// ========================================

async function moveEntry(entry) {
    const destination = prompt(
        "Enter the complete new destination path:",
        joinPath(parentPath(fileManagerPath), entry.name)
    );

    if (destination === null) return;

    if (!destination.trim()) {
        alert("A destination path is required.");
        return;
    }

    if (destination.trim() === entry.path) return;

    try {
        await apiPost("/api/files/move", {
            root: ROOT_ID,
            source_path: entry.path,
            destination_path: destination.trim()
        });

        alert("Move completed.");
        await refreshFileManager();

    } catch (error) {
        showError(error, "Move");
    }
}


// ========================================
// FILE MANAGER: DELETE
// ========================================

async function deleteEntry(entry) {
    const approved = confirm(
        `Are you sure you want to delete this item?\n\n${entry.path}\n\nThis operation cannot be undone.`
    );

    if (!approved) return;

    const typed = prompt(
        'Type DELETE to permanently delete this item:'
    );

    if (typed !== "DELETE") {
        alert("Deletion cancelled. The required confirmation was not entered.");
        return;
    }

    try {
        await apiPost("/api/files/delete", {
            root: ROOT_ID,
            path: entry.path,
            confirm: "DELETE"
        });

        await refreshFileManager();

    } catch (error) {
        showError(error, "Delete");
    }
}


// ========================================
// FILE MANAGER: CONTROLS
// ========================================

if (elements.fmBack) {
    elements.fmBack.addEventListener("click", () => {
        browseFileManager(parentPath(fileManagerPath));
    });
}

if (elements.fmUp) {
    elements.fmUp.addEventListener("click", () => {
        browseFileManager(parentPath(fileManagerPath));
    });
}

if (elements.fmRefresh) {
    elements.fmRefresh.addEventListener("click", refreshFileManager);
}

[
    elements.fmNewFolder,
    elements.fmNewFile,
    elements.fmUpload,
    elements.editorSave
].forEach(button => {
    if (!button || accessPermissions.write_files) return;
    button.disabled = true;
    button.title = "Grant file change access in Access settings.";
});

if (elements.fmNewFile) {
    elements.fmNewFile.addEventListener("click", createNewFile);
}

if (elements.fmNewFolder) {
    elements.fmNewFolder.addEventListener("click", createNewFolder);
}

if (elements.fmUpload) {
    elements.fmUpload.addEventListener("click", () => {
        elements.fmUploadInput.click();
    });
}

if (elements.fmUploadInput) {
    elements.fmUploadInput.addEventListener("change", event => {
        uploadSelectedFile(event.target.files[0]);
    });
}

if (elements.fmSearch) {
    elements.fmSearch.addEventListener("input", () => {
        renderFileManagerEntries(fileManagerEntries);
    });
}

if (elements.editorSave) {
    elements.editorSave.addEventListener("click", saveEditor);
}

if (elements.editorClose) {
    elements.editorClose.addEventListener("click", closeEditor);
}

if (elements.previewClose) {
    elements.previewClose.addEventListener("click", closePreview);
}

if (elements.imageDialogClose) {
    elements.imageDialogClose.addEventListener("click", closeImageDialog);
}

if (elements.imageDialog) {
    elements.imageDialog.addEventListener("click", event => {
        if (event.target === elements.imageDialog) closeImageDialog();
    });
    elements.imageDialog.addEventListener("close", () => {
        elements.imageDialogImage.removeAttribute("src");
        previewFilePath = null;
    });
}

if (elements.imageDialogDownload) {
    elements.imageDialogDownload.addEventListener("click", () => {
        if (previewFilePath) downloadFile(previewFilePath);
    });
}

if (elements.previewEdit) {
    elements.previewEdit.addEventListener("click", () => {
        if (previewFilePath) openTextEditor(previewFilePath);
    });
}

if (elements.previewDownload) {
    elements.previewDownload.addEventListener("click", () => {
        if (previewFilePath) downloadFile(previewFilePath);
    });
}

if (elements.propertiesClose) {
    elements.propertiesClose.addEventListener("click", () => {
        elements.propertiesPanel.hidden = true;
    });
}


// ========================================
// MANUAL DASHBOARD REFRESH
// ========================================

if (elements.refreshButton) {
    elements.refreshButton.addEventListener("click", async () => {
        elements.refreshButton.disabled = true;

        try {
            const updates = [];
            if (accessPermissions.monitoring) {
                updates.push(updateDashboard(), updateStorageMetrics());
            }
            if (accessPermissions.read_files) {
                updates.push(browseStorage(storagePath));
            }
            await Promise.all(updates);
        } finally {
            elements.refreshButton.disabled = false;
        }
    });
}


// ========================================
// AUTOMATIC REFRESH
// ========================================

if (accessPermissions.monitoring) {
    updateDashboard();
    updateStorageMetrics();
    setInterval(updateDashboard, REFRESH_INTERVAL);
    setInterval(updateStorageMetrics, STORAGE_REFRESH_INTERVAL);
} else {
    setText(elements.connectionStatus, "ACCESS NOT GRANTED");
    setText(elements.monitoringState, "ACCESS NOT GRANTED");
    setText(elements.lastUpdated, "Grant system monitoring access to begin.");
}

if (accessPermissions.read_files) {
    browseStorage(ROOT_PATH);
    browseFileManager(ROOT_PATH);
} else {
    elements.browserEntries?.replaceChildren(
        createElement("div", "storage-empty", "Grant file read access in Access settings.")
    );
    elements.fmEntries?.replaceChildren(
        createElement("div", "fm-empty", "Grant file read access in Access settings.")
    );
}


// ========================================
// SIDEBAR NAVIGATION
// ========================================

const navigationLinks = document.querySelectorAll(
    ".nav-link:not(.disabled-link)"
);

const dashboardSections = document.querySelectorAll(
    ".main-content > section[id]"
);

function setActiveLink(sectionId) {
    navigationLinks.forEach(link => {
        link.classList.toggle(
            "active",
            link.getAttribute("href") === "#" + sectionId
        );
    });
}

if ("IntersectionObserver" in window) {
    const sectionObserver = new IntersectionObserver(
        entries => {
            const visible = entries
                .filter(entry => entry.isIntersecting)
                .sort(
                    (a, b) =>
                        a.boundingClientRect.top -
                        b.boundingClientRect.top
                );

            if (visible.length) {
                setActiveLink(visible[0].target.id);
            }
        },
        {
            root: null,
            rootMargin: "-10% 0px -70% 0px",
            threshold: 0
        }
    );

    dashboardSections.forEach(section => {
        sectionObserver.observe(section);
    });
}

navigationLinks.forEach(link => {
    link.addEventListener("click", () => {
        const href = link.getAttribute("href");

        if (href && href.startsWith("#")) {
            setActiveLink(href.substring(1));
        }
    });
});

document.querySelectorAll(".disabled-link").forEach(link => {
    link.addEventListener("click", event => {
        event.preventDefault();
    });
});
