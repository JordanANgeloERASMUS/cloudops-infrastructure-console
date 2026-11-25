"""Authenticated CloudOps telemetry and consent-based file-sharing agent."""

import ipaddress
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request
from werkzeug.utils import secure_filename

from monitoring import get_system_metrics


AGENT_PORT = 8765
TAILSCALE_IPV4 = ipaddress.ip_network("100.64.0.0/10")
MAX_SHARE_BYTES = 10 * 1024 * 1024
MAX_PENDING_SHARES = 3
SHARE_TTL_SECONDS = 10 * 60
WINDOWS_RESERVED_NAMES = re.compile(
    r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)",
    re.IGNORECASE,
)


def create_agent_app(token, approval_token=None):
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("CLOUDOPS_AGENT_TOKEN must contain at least 32 characters.")
    if approval_token is not None and (
        not isinstance(approval_token, str) or len(approval_token) < 32
    ):
        raise ValueError(
            "CLOUDOPS_AGENT_APPROVAL_TOKEN must contain at least 32 characters."
        )
    if approval_token is not None and secrets.compare_digest(token, approval_token):
        raise ValueError(
            "CLOUDOPS_AGENT_APPROVAL_TOKEN must be different from the agent key."
        )

    agent = Flask(__name__)
    agent.config["AGENT_TOKEN"] = token
    agent.config["APPROVAL_TOKEN"] = approval_token
    agent.config["MAX_CONTENT_LENGTH"] = MAX_SHARE_BYTES + 64 * 1024
    agent.config["PENDING_SHARES"] = {}
    pending_lock = threading.Lock()

    @agent.before_request
    def authorize():
        approval_routes = (
            request.path == "/api/shares/pending"
            or request.path.startswith("/api/shares/") and request.path.endswith("/decision")
        )
        expected_token = (
            agent.config["APPROVAL_TOKEN"]
            if approval_routes
            else agent.config["AGENT_TOKEN"]
        )
        if not expected_token:
            return jsonify({
                "error": "Local file-sharing approval is not configured."
            }), 403

        supplied = request.headers.get("Authorization", "")
        expected = f"Bearer {expected_token}"
        if not secrets.compare_digest(supplied, expected):
            return jsonify({"error": "Valid agent access key required."}), 401
        return None

    @agent.after_request
    def secure_response(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @agent.get("/api/health")
    def health():
        return jsonify({"application": "CloudOps Agent"})

    @agent.get("/api/metrics")
    def metrics():
        result = get_system_metrics(
            include_processes=False,
            cpu_interval=None,
        )
        return jsonify(result)

    def prune_pending_shares():
        cutoff = time.time() - SHARE_TTL_SECONDS
        with pending_lock:
            pending = agent.config["PENDING_SHARES"]
            for share_id in [
                share_id
                for share_id, item in pending.items()
                if item["created_at"] < cutoff
            ]:
                pending.pop(share_id, None)

    @agent.post("/api/shares")
    def receive_share_offer():
        if not agent.config["APPROVAL_TOKEN"]:
            return jsonify({
                "error": "File sharing requires a local approval key on this device."
            }), 403
        prune_pending_shares()

        uploaded = request.files.get("file")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "Choose a file to share."}), 400

        filename = secure_filename(uploaded.filename)
        if (
            not filename
            or filename in {".", ".."}
            or WINDOWS_RESERVED_NAMES.match(filename)
        ):
            return jsonify({"error": "The selected filename is not valid."}), 400

        content = uploaded.stream.read(MAX_SHARE_BYTES + 1)
        if not content:
            return jsonify({"error": "Empty files cannot be shared."}), 400
        if len(content) > MAX_SHARE_BYTES:
            return jsonify({
                "error": "Files must be 10 MB or smaller."
            }), 413

        sender = request.form.get("sender", "").strip()
        if not sender or len(sender) > 60 or any(ord(char) < 32 for char in sender):
            return jsonify({"error": "A valid sender name is required."}), 400

        share_id = uuid.uuid4().hex
        with pending_lock:
            pending = agent.config["PENDING_SHARES"]
            if len(pending) >= MAX_PENDING_SHARES:
                return jsonify({
                    "error": "This device already has the maximum number of pending shares."
                }), 429
            pending[share_id] = {
                "id": share_id,
                "filename": filename,
                "size": len(content),
                "sender": sender,
                "created_at": time.time(),
                "content": content,
            }
        return jsonify({
            "id": share_id,
            "filename": filename,
            "size": len(content),
            "status": "awaiting_approval",
        }), 202

    @agent.get("/api/shares/pending")
    def pending_shares():
        prune_pending_shares()
        with pending_lock:
            shares = [
                {
                    "id": item["id"],
                    "filename": item["filename"],
                    "size": item["size"],
                    "sender": item["sender"],
                    "created_at": item["created_at"],
                }
                for item in agent.config["PENDING_SHARES"].values()
            ]
        return jsonify({"shares": shares})

    @agent.post("/api/shares/<share_id>/decision")
    def decide_share(share_id):
        prune_pending_shares()
        decision = request.get_json(silent=True)
        if not isinstance(decision, dict) or decision.get("decision") not in {
            "approve",
            "reject",
        }:
            return jsonify({"error": "Choose approve or reject."}), 400

        with pending_lock:
            item = agent.config["PENDING_SHARES"].get(share_id)
            if item is None:
                return jsonify({
                    "error": "This share expired or was already handled."
                }), 404

            if decision["decision"] == "reject":
                agent.config["PENDING_SHARES"].pop(share_id, None)
                return jsonify({"ok": True, "status": "rejected"})

            destination = Path.home() / "CloudOps Shared" / "Received"
            target = destination / item["filename"]
            descriptor = None
            created = False
            try:
                destination.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                created = True
                with os.fdopen(descriptor, "wb") as file_handle:
                    descriptor = None
                    file_handle.write(item["content"])
            except FileExistsError:
                return jsonify({
                    "error": (
                        "A file with that name is already in CloudOps Shared/Received. "
                        "Rename your local copy before sharing it again."
                    )
                }), 409
            except OSError:
                if descriptor is not None:
                    os.close(descriptor)
                if created:
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        agent.logger.exception(
                            "Unable to remove an incomplete received file"
                        )
                agent.logger.exception("Unable to save an approved file share")
                return jsonify({"error": "Could not save the approved file."}), 500

            agent.config["PENDING_SHARES"].pop(share_id, None)
        return jsonify({
            "ok": True,
            "status": "received",
            "filename": item["filename"],
        })

    return agent


def _configured_bind_address():
    host = os.environ.get("CLOUDOPS_AGENT_BIND", "127.0.0.1").strip()
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise SystemExit(
            "CLOUDOPS_AGENT_BIND must be a loopback or Tailscale IPv4 address."
        ) from error

    if address.is_loopback:
        return str(address)
    if address.version == 4 and address in TAILSCALE_IPV4:
        return str(address)
    raise SystemExit(
        "For remote access, bind only to this PC's Tailscale IPv4 address; "
        "public and all-interface listeners are disabled."
    )


if __name__ == "__main__":
    agent_token = os.environ.get("CLOUDOPS_AGENT_TOKEN", "")
    if len(agent_token) < 32:
        raise SystemExit(
            "Set CLOUDOPS_AGENT_TOKEN to a random secret of at least 32 characters."
        )

    approval_token = os.environ.get("CLOUDOPS_AGENT_APPROVAL_TOKEN", "")
    if approval_token and len(approval_token) < 32:
        raise SystemExit(
            "CLOUDOPS_AGENT_APPROVAL_TOKEN must contain at least 32 characters."
        )

    agent_host = _configured_bind_address()
    print(
        f"Starting CloudOps agent on {agent_host}:{AGENT_PORT}. "
        "File offers are stored only in memory until approved locally; "
        "approved files go to ~/CloudOps Shared/Received."
    )
    create_agent_app(agent_token, approval_token=approval_token).run(
        host=agent_host,
        port=AGENT_PORT,
        debug=False,
        threaded=True,
        use_reloader=False,
    )
