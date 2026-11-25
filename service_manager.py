"""CloudOps systemd service manager (Linux only)."""

import platform
import subprocess

SYSTEMCTL = "/usr/bin/systemctl"
HELPER = "/usr/local/sbin/cloudops-service-control"

ALLOWED_SERVICES = {
    "ollama.service",
    "mssql-server.service",
    "deluged.service",
}

ALLOWED_ACTIONS = {"start", "stop", "restart"}


def _require_systemd():
    if platform.system() != "Linux":
        raise RuntimeError(
            "Systemd service management is available on Linux only."
        )


def _run(command, timeout=20):
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def get_services():
    """Return installed and loaded system services."""
    _require_systemd()

    files = _run([
        SYSTEMCTL, "list-unit-files",
        "--type=service", "--no-legend", "--no-pager"
    ])

    if files.returncode != 0:
        raise RuntimeError(files.stderr.strip() or "Unable to list services.")

    services = {}

    for line in files.stdout.splitlines():
        parts = line.split()

        if len(parts) >= 2 and parts[0].endswith(".service"):
            services[parts[0]] = {
                "name": parts[0],
                "file_state": parts[1],
                "active_state": "inactive",
                "sub_state": "dead",
            }

    loaded = _run([
        SYSTEMCTL, "list-units",
        "--type=service", "--all",
        "--no-legend", "--no-pager", "--plain"
    ])

    if loaded.returncode == 0:
        for line in loaded.stdout.splitlines():
            parts = line.split(None, 4)

            if len(parts) >= 4 and parts[0].endswith(".service"):
                name = parts[0]

                services.setdefault(name, {
                    "name": name,
                    "file_state": "unknown",
                })

                services[name]["active_state"] = parts[2]
                services[name]["sub_state"] = parts[3]

    result = []

    for service in services.values():
        service["manageable"] = (
            service["name"] in ALLOWED_SERVICES
        )
        result.append(service)

    result.sort(key=lambda item: item["name"].lower())

    return {
        "services": result,
        "total": len(result),
        "manageable": len(ALLOWED_SERVICES),
    }


def control_service(name, action):
    """Control only explicitly approved system services."""
    _require_systemd()

    if name not in ALLOWED_SERVICES:
        raise PermissionError("This service is not approved for control.")

    if action not in ALLOWED_ACTIONS:
        raise ValueError("Invalid service action.")

    result = _run([
        "sudo", "-n", HELPER, action, name
    ], timeout=35)

    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(message or "Service operation failed.")

    return {
        "ok": True,
        "service": name,
        "action": action,
        "message": result.stdout.strip() or
                   f"{action.title()} request completed for {name}.",
    }
