"""Authenticated CloudOps agent connections and consent-based file sharing."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid

from werkzeug.utils import secure_filename


AGENT_PORT = 8765
TAILSCALE_IPV4 = ipaddress.ip_network("100.64.0.0/10")
MAX_DEVICES = 10
MAX_RESPONSE_BYTES = 64 * 1024
MAX_SHARE_BYTES = 10 * 1024 * 1024
_DEVICES = {}
_DEVICES_LOCK = threading.Lock()


class RemoteDeviceError(ValueError):
    """A device could not be validated or reached safely."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


def _validate_host(host):
    if not isinstance(host, str) or not host.strip():
        raise RemoteDeviceError("Enter the Windows device's Tailscale IPv4 address.")

    try:
        address = ipaddress.ip_address(host.strip())
    except ValueError as error:
        raise RemoteDeviceError(
            "Use the device's Tailscale IPv4 address, not a hostname or public IP."
        ) from error

    if address.version != 4 or address not in TAILSCALE_IPV4:
        raise RemoteDeviceError(
            "Remote devices must use their Tailscale IPv4 address (100.64.0.0/10)."
        )

    return str(address)


def _validate_name(name):
    if not isinstance(name, str):
        raise RemoteDeviceError("A device name is required.")
    name = name.strip()
    if not name or len(name) > 60 or any(ord(char) < 32 for char in name):
        raise RemoteDeviceError("Device name must be 1–60 printable characters.")
    return name


def _validate_token(token):
    if (
        not isinstance(token, str)
        or not 32 <= len(token) <= 256
        or re.fullmatch(r"[A-Za-z0-9._~-]+", token) is None
    ):
        raise RemoteDeviceError(
            "Enter the agent's generated access key (at least 32 characters)."
        )
    return token


def _fetch_json(host, token, endpoint):
    url = f"http://{host}:{AGENT_PORT}{endpoint}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "CloudOps-ReadOnly-Monitor/1.0",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )

    try:
        with opener.open(request, timeout=3) as response:
            if response.status != 200:
                raise RemoteDeviceError(
                    f"Agent returned HTTP {response.status}."
                )
            if response.headers.get_content_type() != "application/json":
                raise RemoteDeviceError("Agent returned an unexpected response type.")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code in {301, 302, 303, 307, 308}:
            raise RemoteDeviceError("Agent redirects are not accepted.") from error
        if error.code in {401, 403}:
            raise RemoteDeviceError("The agent rejected this access key.") from error
        raise RemoteDeviceError(
            f"Agent returned HTTP {error.code}."
        ) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RemoteDeviceError(
            "Could not reach the agent. Check Tailscale, its IP and that it is running."
        ) from error

    if len(raw) > MAX_RESPONSE_BYTES:
        raise RemoteDeviceError("Agent response exceeded the size limit.")

    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RemoteDeviceError("Agent returned invalid JSON.") from error

    if not isinstance(result, dict):
        raise RemoteDeviceError("Agent returned an invalid response.")
    return result


def add_device(name, host, token):
    """Verify an agent and keep its access key in memory for this app run."""
    name = _validate_name(name)
    host = _validate_host(host)
    token = _validate_token(token)

    health = _fetch_json(host, token, "/api/health")
    if health.get("application") != "CloudOps Agent":
        raise RemoteDeviceError("The device is not running a compatible CloudOps agent.")

    metrics = _fetch_json(host, token, "/api/metrics")
    system = metrics.get("system")
    if not isinstance(system, dict) or not system.get("hostname"):
        raise RemoteDeviceError("Agent metrics are missing device identity.")

    with _DEVICES_LOCK:
        if len(_DEVICES) >= MAX_DEVICES:
            raise RemoteDeviceError(f"A maximum of {MAX_DEVICES} devices can be paired.")
        if any(device["host"] == host for device in _DEVICES.values()):
            raise RemoteDeviceError("This Tailscale address is already paired.")

        device_id = uuid.uuid4().hex
        _DEVICES[device_id] = {
            "id": device_id,
            "name": name,
            "host": host,
            "token": token,
            "hostname": system["hostname"],
            "operating_system": system.get("operating_system", "Unknown"),
            "connected_at": time.time(),
            "last_seen": time.time(),
        }

    return {
        "id": device_id,
        "name": name,
        "host": host,
        "hostname": system["hostname"],
        "operating_system": system.get("operating_system", "Unknown"),
        "metrics": metrics,
    }


def list_devices():
    """List paired devices without exposing their access keys."""
    with _DEVICES_LOCK:
        return [
            {
                "id": device["id"],
                "name": device["name"],
                "host": device["host"],
                "hostname": device["hostname"],
                "operating_system": device["operating_system"],
                "connected_at": device["connected_at"],
                "last_seen": device["last_seen"],
            }
            for device in _DEVICES.values()
        ]


def get_device_metrics(device_id):
    with _DEVICES_LOCK:
        device = _DEVICES.get(device_id)
        if device is None:
            raise RemoteDeviceError("The paired device was not found.")
        host = device["host"]
        token = device["token"]

    metrics = _fetch_json(host, token, "/api/metrics")
    system = metrics.get("system")
    if not isinstance(system, dict) or system.get("hostname") != device["hostname"]:
        raise RemoteDeviceError(
            "The connected device identity changed. Remove it and pair it again."
        )

    with _DEVICES_LOCK:
        current = _DEVICES.get(device_id)
        if current is None or current["token"] != token:
            raise RemoteDeviceError("The paired device was removed.")
        current["last_seen"] = time.time()
    return metrics


def send_file_to_device(device_id, filename, content):
    if not isinstance(filename, str):
        raise RemoteDeviceError("Choose a file with a valid name.")
    filename = secure_filename(filename)
    if (
        not filename
        or filename in {".", ".."}
        or re.match(
            r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)",
            filename,
            re.IGNORECASE,
        )
    ):
        raise RemoteDeviceError("The selected filename is not valid.")
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_SHARE_BYTES:
        raise RemoteDeviceError("Choose a non-empty file of 10 MB or smaller.")

    with _DEVICES_LOCK:
        device = _DEVICES.get(device_id)
        if device is None:
            raise RemoteDeviceError("The paired device was not found.")
        host = device["host"]
        token = device["token"]
        expected_hostname = device["hostname"]
        sender = device["name"]

    health = _fetch_json(host, token, "/api/health")
    if health.get("application") != "CloudOps Agent":
        raise RemoteDeviceError("The paired device is not running a compatible agent.")
    metrics = _fetch_json(host, token, "/api/metrics")
    system = metrics.get("system")
    if not isinstance(system, dict) or system.get("hostname") != expected_hostname:
        raise RemoteDeviceError(
            "The connected device identity changed. Remove it and pair it again."
        )

    boundary = secrets.token_hex(16)
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        (
            'Content-Disposition: form-data; name="sender"\r\n\r\n'
            f"{sender}\r\n"
        ).encode(),
        f"--{boundary}\r\n".encode(),
        (
            'Content-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode(),
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    url = f"http://{host}:{AGENT_PORT}/api/shares"
    outbound = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "CloudOps-File-Share/1.0",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )
    try:
        with opener.open(outbound, timeout=30) as response:
            if response.status != 202:
                raise RemoteDeviceError(f"Agent returned HTTP {response.status}.")
            result = json.loads(response.read(MAX_RESPONSE_BYTES + 1))
    except urllib.error.HTTPError as error:
        try:
            details = json.loads(error.read(MAX_RESPONSE_BYTES + 1))
        except (json.JSONDecodeError, UnicodeDecodeError):
            details = {}
        if error.code in {401, 403}:
            raise RemoteDeviceError("The agent rejected this access key.") from error
        message = details.get("error")
        if error.code in {400, 413, 429} and isinstance(message, str):
            raise RemoteDeviceError(message) from error
        raise RemoteDeviceError(
            f"Agent returned HTTP {error.code} while sending the file."
        ) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RemoteDeviceError(
            "Could not reach the device. Check Tailscale and that its agent is running."
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RemoteDeviceError("Agent returned an invalid share response.") from error

    if not isinstance(result, dict) or result.get("status") != "awaiting_approval":
        raise RemoteDeviceError("Agent returned an invalid share response.")
    with _DEVICES_LOCK:
        current = _DEVICES.get(device_id)
        if current is None or current["token"] != token:
            raise RemoteDeviceError("The paired device was removed.")
        current["last_seen"] = time.time()
    return result


def _local_agent_credentials(approval=False):
    host = os.environ.get("CLOUDOPS_AGENT_BIND", "127.0.0.1").strip()
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise RemoteDeviceError("The local agent bind address is invalid.") from error
    if not address.is_loopback and not (
        address.version == 4 and address in TAILSCALE_IPV4
    ):
        raise RemoteDeviceError("The local agent must use loopback or Tailscale.")

    variable = (
        "CLOUDOPS_AGENT_APPROVAL_TOKEN"
        if approval
        else "CLOUDOPS_AGENT_TOKEN"
    )
    token = os.environ.get(variable, "")
    if not token:
        raise RemoteDeviceError(
            f"Set {variable} in the CloudOps dashboard environment to use file sharing."
        )
    return str(address), _validate_token(token)


def _local_agent_request(endpoint, approval=False, body=None):
    host, token = _local_agent_credentials(approval=approval)
    data = None if body is None else json.dumps(body).encode()
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "CloudOps-Local-Share-Approval/1.0",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    outbound = urllib.request.Request(
        f"http://{host}:{AGENT_PORT}{endpoint}",
        data=data,
        headers=headers,
        method="GET" if data is None else "POST",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )
    try:
        with opener.open(outbound, timeout=3) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if response.status != 200:
                raise RemoteDeviceError(f"Local agent returned HTTP {response.status}.")
    except urllib.error.HTTPError as error:
        try:
            details = json.loads(error.read(MAX_RESPONSE_BYTES + 1))
        except (json.JSONDecodeError, UnicodeDecodeError):
            details = {}
        message = details.get("error")
        if isinstance(message, str):
            raise RemoteDeviceError(message) from error
        raise RemoteDeviceError(f"Local agent returned HTTP {error.code}.") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RemoteDeviceError(
            "The local file-sharing agent is unavailable. Start it on this device."
        ) from error
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RemoteDeviceError("Local agent response exceeded the size limit.")
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RemoteDeviceError("Local agent returned invalid JSON.") from error
    if not isinstance(result, dict):
        raise RemoteDeviceError("Local agent returned an invalid response.")
    return result


def get_local_share_offers():
    result = _local_agent_request("/api/shares/pending", approval=True)
    shares = result.get("shares")
    if not isinstance(shares, list) or len(shares) > 3:
        raise RemoteDeviceError("Local agent returned an invalid share inbox.")
    return shares


def decide_local_share(share_id, decision):
    if not isinstance(share_id, str) or re.fullmatch(r"[a-f0-9]{32}", share_id) is None:
        raise RemoteDeviceError("The share request ID is invalid.")
    if decision not in {"approve", "reject"}:
        raise RemoteDeviceError("Choose approve or reject.")
    return _local_agent_request(
        f"/api/shares/{share_id}/decision",
        approval=True,
        body={"decision": decision},
    )


def remove_device(device_id):
    with _DEVICES_LOCK:
        device = _DEVICES.pop(device_id, None)
    if device is None:
        raise RemoteDeviceError("The paired device was not found.")
    return {
        "id": device_id,
        "name": device["name"],
        "host": device["host"],
    }
