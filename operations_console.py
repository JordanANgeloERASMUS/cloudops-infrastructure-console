"""Fixed, read-only diagnostics for the local operations console."""

import os
import platform
import socket
import time

import psutil

from monitoring import get_storage_metrics, get_uptime
from process_manager import get_processes


CONSOLE_ACTIONS = {
    "system": "System summary",
    "storage": "Storage summary",
    "network": "Network summary",
    "processes": "Top processes",
}


def _system_summary():
    memory = psutil.virtual_memory()
    load_average = os.getloadavg() if hasattr(os, "getloadavg") else None
    return {
        "hostname": socket.gethostname(),
        "operating_system": f"{platform.system()} {platform.release()}",
        "architecture": platform.machine(),
        "cpu_cores": psutil.cpu_count(logical=True),
        "cpu_usage_percent": round(psutil.cpu_percent(interval=0.1), 1),
        "memory_usage_percent": round(memory.percent, 1),
        "memory_available_gb": round(memory.available / (1024 ** 3), 2),
        "load_average": (
            [round(value, 2) for value in load_average]
            if load_average is not None else None
        ),
        "uptime": get_uptime()["formatted"],
    }


def _network_summary():
    counters = psutil.net_io_counters()
    interfaces = psutil.net_if_stats()
    return {
        "bytes_sent": counters.bytes_sent,
        "bytes_received": counters.bytes_recv,
        "interfaces": [
            {"name": name, "up": stats.isup, "speed_mbps": stats.speed}
            for name, stats in sorted(interfaces.items())
        ],
    }


def _process_summary():
    snapshot = get_processes(sort_by="memory")
    return {
        "total": snapshot["total"],
        "top": [
            {
                "pid": process["pid"],
                "name": process["name"],
                "memory_percent": process["memory_percent"],
                "cpu_percent": process["cpu_percent"],
            }
            for process in snapshot["processes"][:10]
        ],
    }


_DIAGNOSTICS = {
    "system": _system_summary,
    "storage": get_storage_metrics,
    "network": _network_summary,
    "processes": _process_summary,
}


def run_diagnostic(action):
    """Run one of the fixed local diagnostics; never accepts command text."""
    diagnostic = _DIAGNOSTICS.get(action)
    if diagnostic is None:
        raise ValueError("Unknown diagnostic action.")

    return {
        "action": action,
        "label": CONSOLE_ACTIONS[action],
        "generated_at": time.time(),
        "result": diagnostic(),
    }
