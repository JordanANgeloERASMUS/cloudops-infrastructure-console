
import os
import platform
import socket
import time
from datetime import datetime
from pathlib import Path

import psutil

# Prime non-blocking CPU sampling so the first live reading is meaningful.
psutil.cpu_percent(interval=None)


# ============================================================
# CONFIGURATION
# ============================================================

from file_manager import get_allowed_storage_roots, get_directory_listing


# ============================================================
# HELPERS
# ============================================================

def bytes_to_gb(value):
    return round(value / (1024 ** 3), 2)


def get_uptime():
    boot_time = psutil.boot_time()
    uptime_seconds = int(time.time() - boot_time)

    days, remainder = divmod(uptime_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    return {
        "seconds": uptime_seconds,
        "formatted": f"{days}d {hours}h {minutes}m",
        "boot_time": datetime.fromtimestamp(boot_time).isoformat()
    }


def build_dashboard_alerts(metrics):
    """Return explainable alerts for resource values in a metrics snapshot."""
    alerts = []

    def add_threshold_alert(metric_id, label, value, warning, critical, unit="%"):
        if value is None:
            return

        try:
            value = float(value)
        except (TypeError, ValueError):
            return

        if value >= critical:
            severity = "critical"
            threshold = critical
        elif value >= warning:
            severity = "warning"
            threshold = warning
        else:
            return

        alerts.append({
            "id": f"{metric_id}:{severity}",
            "severity": severity,
            "title": f"{label} usage is {severity}",
            "message": (
                f"{value:.1f}{unit} used; {severity} alert threshold "
                f"is {threshold}{unit}."
            ),
        })

    add_threshold_alert(
        "cpu", "CPU", metrics.get("cpu_usage"), warning=85, critical=95
    )
    add_threshold_alert(
        "memory", "Memory", metrics.get("memory_usage"),
        warning=85, critical=95
    )
    add_threshold_alert(
        "filesystem-root", "Root filesystem", metrics.get("disk_usage"),
        warning=85, critical=95
    )

    for partition in metrics.get("disk_partitions", []):
        mountpoint = partition.get("mountpoint")
        primary_mountpoint = metrics.get("disk_mountpoint", "/")
        if not mountpoint or os.path.normcase(
            os.path.normpath(mountpoint)
        ) == os.path.normcase(os.path.normpath(primary_mountpoint)):
            continue
        add_threshold_alert(
            f"filesystem:{mountpoint}",
            f"Filesystem {mountpoint}",
            partition.get("usage_percent"),
            warning=85,
            critical=95,
        )

    return alerts


def get_unique_disk_partitions():
    partitions = psutil.disk_partitions(all=False)
    unique_devices = {}

    if platform.system() == "Windows":
        for partition in partitions:
            mountpoint = partition.mountpoint or partition.device
            if not mountpoint or "cdrom" in (partition.opts or "").lower():
                continue

            key = os.path.normcase(os.path.normpath(mountpoint))
            unique_devices.setdefault(key, partition)

        return list(unique_devices.values())

    for partition in partitions:
        device = partition.device
        filesystem = partition.fstype.lower()

        if filesystem == "squashfs":
            continue

        if device.startswith("/dev/loop"):
            continue

        if not device.startswith("/dev/"):
            continue

        existing = unique_devices.get(device)

        if existing is None:
            unique_devices[device] = partition
            continue

        def mount_priority(path):
            if path == "/":
                return (0, 0)
            if path == "/home":
                return (1, 0)
            return (2, len(path))

        if mount_priority(partition.mountpoint) < mount_priority(
            existing.mountpoint
        ):
            unique_devices[device] = partition

    return list(unique_devices.values())


def _primary_disk_path():
    if platform.system() == "Windows":
        system_drive = os.environ.get("SystemDrive")
        return system_drive + os.sep if system_drive else Path.home().anchor
    return "/"


# ============================================================
# SYSTEM MONITORING
# ============================================================

def get_system_metrics(include_processes=True, cpu_interval=0.2):
    cpu_usage = psutil.cpu_percent(interval=cpu_interval)

    memory = psutil.virtual_memory()
    disk_mountpoint = _primary_disk_path()

    try:
        root_disk = psutil.disk_usage(disk_mountpoint)

        disk_usage = root_disk.percent
        disk_total = bytes_to_gb(root_disk.total)
        disk_used = bytes_to_gb(root_disk.used)
        disk_free = bytes_to_gb(root_disk.free)

    except OSError:
        disk_usage = 0
        disk_total = 0
        disk_used = 0
        disk_free = 0

    disk_partitions = []

    for partition in get_unique_disk_partitions():
        try:
            usage = psutil.disk_usage(partition.mountpoint)

            disk_partitions.append({
                "device": partition.device,
                "mountpoint": partition.mountpoint,
                "filesystem": partition.fstype,
                "total_gb": bytes_to_gb(usage.total),
                "used_gb": bytes_to_gb(usage.used),
                "free_gb": bytes_to_gb(usage.free),
                "usage_percent": usage.percent
            })

        except (PermissionError, OSError):
            continue

    network = psutil.net_io_counters()

    processes = []
    if include_processes:
        for process in psutil.process_iter(
            ["pid", "name", "username", "memory_percent", "cpu_percent"]
        ):
            try:
                processes.append(process.info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        processes.sort(
            key=lambda item: item.get("memory_percent") or 0,
            reverse=True
        )

    system_information = {
        "hostname": socket.gethostname(),
        "operating_system": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True)
    }

    result = {
        "cpu_usage": round(cpu_usage, 1),

        "memory_usage": round(memory.percent, 1),
        "memory_total": bytes_to_gb(memory.total),
        "memory_used": bytes_to_gb(memory.used),
        "memory_available": bytes_to_gb(memory.available),

        "disk_usage": round(disk_usage, 1),
        "disk_total": disk_total,
        "disk_used": disk_used,
        "disk_free": disk_free,
        "disk_mountpoint": disk_mountpoint,
        "disk_partitions": disk_partitions,

        "network": {
            "bytes_sent": network.bytes_sent,
            "bytes_received": network.bytes_recv,
            "megabytes_sent": round(
                network.bytes_sent / (1024 ** 2), 2
            ),
            "megabytes_received": round(
                network.bytes_recv / (1024 ** 2), 2
            )
        },

        "system": system_information,
        "uptime": get_uptime()
    }
    if include_processes:
        result["processes"] = processes[:10]
    return result


# ============================================================
# STORAGE METRICS
# ============================================================

def get_storage_metrics():
    devices = []

    total_capacity = 0
    total_used = 0
    total_free = 0

    for partition in get_unique_disk_partitions():
        try:
            usage = psutil.disk_usage(partition.mountpoint)

            devices.append({
                "device": partition.device,
                "mountpoint": partition.mountpoint,
                "filesystem": partition.fstype,
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "total_gb": bytes_to_gb(usage.total),
                "used_gb": bytes_to_gb(usage.used),
                "free_gb": bytes_to_gb(usage.free),
                "usage_percent": usage.percent
            })

            total_capacity += usage.total
            total_used += usage.used
            total_free += usage.free

        except (PermissionError, OSError):
            continue

    overall_usage = (
        round(total_used / total_capacity * 100, 1)
        if total_capacity else 0
    )

    return {
        "summary": {
            "device_count": len(devices),
            "total_gb": bytes_to_gb(total_capacity),
            "used_gb": bytes_to_gb(total_used),
            "free_gb": bytes_to_gb(total_free),
            "usage_percent": overall_usage
        },
        "devices": devices
    }


# ============================================================
# DIRECT TEST
# ============================================================

if __name__ == "__main__":
    print("\n========================================")
    print("          CLOUDOPS SYSTEM MONITOR")
    print("========================================")

    metrics = get_system_metrics()
    storage = get_storage_metrics()

    print("\nCPU:", metrics["cpu_usage"], "%")
    print("Memory:", metrics["memory_usage"], "%")
    print("Disk:", metrics["disk_usage"], "%")

    print("\nStorage devices:", storage["summary"]["device_count"])

    print("\nFilesystem roots:")

    for root in get_allowed_storage_roots():
        print(root["name"], ":", root["path"])

    print("\nRoot directory preview:")

    try:
        listing = get_directory_listing("system", "")

        for entry in listing["entries"][:20]:
            print(
                entry["type"],
                entry["name"],
                entry["permissions"]
            )

    except Exception as error:
        print("Filesystem error:", error)

    print("\nTEST COMPLETE\n")
