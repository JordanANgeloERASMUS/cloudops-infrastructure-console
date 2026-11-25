
import logging
import os
import platform
import secrets
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request, send_file, session
from werkzeug.exceptions import RequestEntityTooLarge

from file_manager import (
    FileConflictError,
    FileManagerError,
    chmod_path,
    copy_path,
    create_file,
    create_folder,
    delete_path,
    download_path,
    file_preview_path,
    get_allowed_storage_roots,
    get_directory_listing,
    get_properties,
    move_path,
    read_docx_preview,
    read_text_file,
    rename_path,
    save_text_file,
    save_upload,
)

from monitoring import (
    build_dashboard_alerts,
    get_storage_metrics,
    get_system_metrics,
)
from operations_console import CONSOLE_ACTIONS, run_diagnostic

from process_manager import (
    get_processes,
    get_process_details,
    terminate_process,
)
from remote_devices import (
    RemoteDeviceError,
    add_device,
    decide_local_share,
    get_local_share_offers,
    get_device_metrics,
    list_devices,
    remove_device,
    send_file_to_device,
)


app = Flask(__name__)

app.config.update(
    SECRET_KEY=os.environ.get(
        "CLOUDOPS_SECRET_KEY",
        secrets.token_urlsafe(32),
    ),
    MAX_CONTENT_LENGTH=32 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
)


# ========================================
# AUDIT LOGGING
# ========================================

def _configure_audit_log():
    directory = Path(__file__).resolve().parent / "logs"
    directory.mkdir(exist_ok=True)

    handler = RotatingFileHandler(
        directory / "cloudops_audit.log",
        maxBytes=1_000_000,
        backupCount=3,
    )

    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s"
        )
    )

    logger = logging.getLogger("cloudops.audit")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        logger.addHandler(handler)

    return logger


audit_logger = _configure_audit_log()


# ========================================
# SECURITY / CSRF
# ========================================

def _csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)

    return session["csrf_token"]


def _same_origin_request():
    origin = request.headers.get("Origin") or request.headers.get("Referer")

    if not origin:
        return False

    parsed = urlparse(origin)

    return (
        parsed.scheme in {"http", "https"}
        and parsed.netloc == request.host
    )


def mutation_endpoint(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        token = request.headers.get("X-CSRF-Token", "")

        if (
            not _same_origin_request()
            or not secrets.compare_digest(token, _csrf_token())
        ):
            return jsonify({
                "error": "A valid same-origin CSRF token is required."
            }), 403
        return view(*args, **kwargs)

    return wrapped


def _required_permission():
    path = request.path
    method = request.method

    if path == "/api/metrics" or path == "/api/storage":
        return "monitoring"
    if path.startswith("/api/console/"):
        return "monitoring"
    if path == "/api/processes/terminate":
        return "control_processes"
    if path == "/api/shares/pending" or (
        path.startswith("/api/shares/") and path.endswith("/decision")
    ):
        return "share_files"
    if path.startswith("/api/remote/devices/") and path.endswith("/shares"):
        return "share_files"
    if path.startswith("/api/remote/"):
        return "remote_access"
    if path == "/api/processes" or path.startswith("/api/processes/"):
        return "monitoring"
    if path in {"/api/storage/roots", "/api/storage/browse"}:
        return "read_files"
    if path.startswith("/api/files/"):
        return "read_files" if method in {"GET", "HEAD"} else "write_files"
    return None


@app.before_request
def enforce_access_grants():
    permission = _required_permission()
    if permission is None:
        return None

    if not session.get("access_configured"):
        return jsonify({
            "error": "Choose CloudOps access permissions before continuing."
        }), 403

    grants = session.get("access_permissions", {})
    if not grants.get(permission):
        return jsonify({
            "error": (
                f"CloudOps does not have {permission.replace('_', ' ')} "
                "permission. Change access in the dashboard."
            )
        }), 403
    if permission == "share_files" and not grants.get("remote_access"):
        return jsonify({
            "error": "Pair remote devices before using file sharing."
        }), 403

    return None


def _json_body():
    body = request.get_json(silent=True)

    if not isinstance(body, dict):
        raise FileManagerError("A JSON object is required.")

    return body


def _root(data):
    return data.get("root", "system")


def _audit(action, source=None, destination=None):
    audit_logger.info(
        "action=%s remote=%s source=%r destination=%r",
        action,
        request.remote_addr,
        source,
        destination,
    )


# ========================================
# ERROR HANDLERS
# ========================================

@app.errorhandler(FileConflictError)
def handle_conflict(error):
    return jsonify({"error": str(error)}), 409


@app.errorhandler(FileManagerError)
def handle_file_error(error):
    return jsonify({"error": str(error)}), 400


@app.errorhandler(RequestEntityTooLarge)
def handle_large_upload(error):
    return jsonify({
        "error": "Uploads are limited to 32 MiB."
    }), 413


# ========================================
# MAIN DASHBOARD
# ========================================

@app.route("/")
def home():
    roots = get_allowed_storage_roots()
    return render_template(
        "index.html",
        csrf_token=_csrf_token(),
        access_configured=session.get("access_configured", False),
        access_permissions=session.get("access_permissions", {}),
        operating_system=platform.system(),
        storage_root_path=roots[0]["path"],
        storage_root_label=roots[0]["name"],
    )


@app.route("/api/csrf")
def csrf():
    response = jsonify({
        "csrf_token": _csrf_token()
    })

    response.headers["Cache-Control"] = "no-store"

    return response


@app.route("/api/permissions", methods=["POST"])
@mutation_endpoint
def save_access_permissions():
    data = _json_body()
    permission_names = {
        "monitoring",
        "read_files",
        "write_files",
        "control_processes",
        "remote_access",
        "share_files",
    }

    if set(data) != permission_names or any(
        not isinstance(data[name], bool) for name in permission_names
    ):
        return jsonify({
            "error": "Choose a true or false value for each access permission."
        }), 400

    if data["write_files"] and not data["read_files"]:
        return jsonify({
            "error": "File write access requires file read access."
        }), 400
    if data["share_files"] and not data["remote_access"]:
        return jsonify({
            "error": "File sharing requires remote device access."
        }), 400

    if platform.system() == "Windows" and data["control_processes"]:
        return jsonify({
            "error": (
                "Process termination is unavailable on Windows. "
                "Use Task Manager for process control."
            )
        }), 400

    session["access_configured"] = True
    session["access_permissions"] = data
    _audit("access_permissions", source=",".join(
        name for name, enabled in data.items() if enabled
    ))

    return jsonify({"ok": True, "permissions": data})


@app.route("/api/remote/devices", methods=["GET", "POST"])
def remote_devices():
    if request.method == "GET":
        response = jsonify({"devices": list_devices()})
        response.headers["Cache-Control"] = "no-store"
        return response

    return connect_remote_device()


@app.route("/api/remote/devices/<device_id>/metrics")
def remote_device_metrics(device_id):
    try:
        response = jsonify(get_device_metrics(device_id))
        response.headers["Cache-Control"] = "no-store"
        return response
    except RemoteDeviceError as error:
        return jsonify({"error": str(error)}), 400
    except Exception:
        app.logger.exception("Unable to retrieve remote device metrics")
        return jsonify({"error": "Unable to retrieve remote device metrics"}), 502


@app.route("/api/remote/devices/<device_id>/shares", methods=["POST"])
@mutation_endpoint
def remote_device_share_file(device_id):
    if request.content_length and request.content_length > 10 * 1024 * 1024 + 64 * 1024:
        return jsonify({"error": "Files must be 10 MB or smaller."}), 413
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "Choose a file to share."}), 400
    content = uploaded.stream.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        return jsonify({"error": "Files must be 10 MB or smaller."}), 413
    try:
        result = send_file_to_device(
            device_id,
            uploaded.filename,
            content,
        )
        _audit(
            "remote_file_share_offer",
            source=device_id,
        )
        return jsonify(result), 202
    except RemoteDeviceError as error:
        return jsonify({"error": str(error)}), 400
    except Exception:
        app.logger.exception("Unable to send remote file-share offer")
        return jsonify({"error": "Unable to send the file-share offer"}), 502


@app.route("/api/shares/pending")
def pending_file_shares():
    try:
        response = jsonify({"shares": get_local_share_offers()})
        response.headers["Cache-Control"] = "no-store"
        return response
    except RemoteDeviceError as error:
        return jsonify({"error": str(error)}), 503


@app.route("/api/shares/<share_id>/decision", methods=["POST"])
@mutation_endpoint
def decide_file_share(share_id):
    data = _json_body()
    try:
        result = decide_local_share(share_id, data.get("decision"))
        _audit(
            "remote_file_share_decision",
            source=f"{share_id}:{result.get('status', 'unknown')}",
        )
        return jsonify(result)
    except RemoteDeviceError as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/remote/devices/<device_id>", methods=["DELETE"])
@mutation_endpoint
def disconnect_remote_device(device_id):
    try:
        device = remove_device(device_id)
        _audit(
            "remote_device_disconnect",
            source=f"{device['name']} ({device['host']})",
        )
        return jsonify({"ok": True, "device": device})
    except RemoteDeviceError as error:
        return jsonify({"error": str(error)}), 404


@mutation_endpoint
def connect_remote_device():
    try:
        data = _json_body()
        device = add_device(
            data.get("name"),
            data.get("host"),
            data.get("token"),
        )
        _audit(
            "remote_device_connect",
            source=f"{device['name']} ({device['host']})",
        )
        return jsonify({"ok": True, "device": device}), 201
    except RemoteDeviceError as error:
        return jsonify({"error": str(error)}), 400
    except Exception:
        app.logger.exception("Unable to pair remote device")
        return jsonify({"error": "Unable to pair the remote device"}), 502


# ========================================
# SYSTEM MONITORING
# ========================================

@app.route("/api/metrics")
def metrics():
    try:
        result = get_system_metrics(
            include_processes=False,
            cpu_interval=None,
        )
        result["alerts"] = build_dashboard_alerts(result)
        return jsonify(result)

    except Exception:
        app.logger.exception(
            "Unable to retrieve system metrics"
        )

        return jsonify({
            "error": "Unable to retrieve system metrics"
        }), 500


@app.route("/api/console/actions")
def console_actions():
    return jsonify({
        "actions": [
            {"id": action, "label": label}
            for action, label in CONSOLE_ACTIONS.items()
        ]
    })


@app.route("/api/console/<action>")
def console_diagnostic(action):
    try:
        response = jsonify(run_diagnostic(action))
        response.headers["Cache-Control"] = "no-store"
        return response
    except ValueError as error:
        return jsonify({"error": str(error)}), 404
    except Exception:
        app.logger.exception("Unable to run console diagnostic %s", action)
        return jsonify({"error": "Unable to run the requested diagnostic"}), 500


@app.route("/api/storage")
def storage():
    try:
        return jsonify(get_storage_metrics())

    except Exception:
        app.logger.exception(
            "Unable to retrieve storage information"
        )

        return jsonify({
            "error": "Unable to retrieve storage information"
        }), 500


# ========================================
# PROCESS MANAGER
# ========================================

@app.route("/api/processes")
def processes():
    """
    Retrieve the current process list.

    Supported sorting:
    cpu, memory, pid, name
    """

    try:
        sort_by = request.args.get("sort", "cpu")

        if sort_by not in {
            "cpu",
            "memory",
            "pid",
            "name",
        }:
            return jsonify({
                "error": "Invalid sorting option."
            }), 400

        result = get_processes(sort_by=sort_by)

        response = jsonify(result)
        response.headers["Cache-Control"] = "no-store"

        return response

    except Exception:
        app.logger.exception(
            "Unable to retrieve process information"
        )

        return jsonify({
            "error": "Unable to retrieve process information"
        }), 500


@app.route("/api/processes/<int:pid>")
def process_details(pid):
    """Retrieve details for one process."""

    try:
        result = get_process_details(pid)

        response = jsonify(result)
        response.headers["Cache-Control"] = "no-store"

        return response

    except ValueError as error:
        return jsonify({
            "error": str(error)
        }), 404

    except PermissionError as error:
        return jsonify({
            "error": str(error)
        }), 403

    except Exception:
        app.logger.exception(
            "Unable to retrieve details for PID %s",
            pid,
        )

        return jsonify({
            "error": "Unable to retrieve process details"
        }), 500


@app.route("/api/processes/terminate", methods=["POST"])
@mutation_endpoint
def process_terminate():
    """Request graceful termination of an eligible process."""

    try:
        data = _json_body()

        result = terminate_process(
            data.get("pid"),
            data.get("create_time"),
        )

        _audit(
            "process_terminate",
            source=f"PID {result['pid']} ({result['name']})",
        )

        return jsonify({
            "ok": True,
            **result,
        })

    except ValueError as error:
        return jsonify({
            "error": str(error)
        }), 400

    except PermissionError as error:
        return jsonify({
            "error": str(error)
        }), 403

    except Exception:
        app.logger.exception(
            "Unable to terminate requested process"
        )

        return jsonify({
            "error": "Unable to terminate the requested process"
        }), 500


# ========================================
# STORAGE MANAGER
# ========================================

@app.route("/api/storage/roots")
def storage_roots():
    return jsonify({
        "roots": get_allowed_storage_roots()
    })


@app.route("/api/storage/browse")
def storage_browse():
    return jsonify(
        get_directory_listing(
            request.args.get("root", "system"),
            request.args.get("path", ""),
        )
    )


# ========================================
# FILE MANAGER - READ OPERATIONS
# ========================================

@app.route("/api/files/properties")
def file_properties():
    return jsonify(
        get_properties(
            request.args.get("root", "system"),
            request.args.get("path", ""),
        )
    )


@app.route("/api/files/read")
def file_read():
    return jsonify(
        read_text_file(
            request.args.get("root", "system"),
            request.args.get("path", ""),
        )
    )


@app.route("/api/files/preview")
def file_preview():
    path, mime_type = file_preview_path(
        request.args.get("root", "system"),
        request.args.get("path", ""),
    )

    response = send_file(
        path,
        conditional=True,
        as_attachment=False,
        download_name=path.name,
        mimetype=mime_type,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/files/docx-preview")
def file_docx_preview():
    response = jsonify(
        read_docx_preview(
            request.args.get("root", "system"),
            request.args.get("path", ""),
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/files/download")
def file_download():
    path = download_path(
        request.args.get("root", "system"),
        request.args.get("path", ""),
    )

    response = send_file(
        path,
        conditional=True,
        as_attachment=True,
        download_name=path.name,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


# ========================================
# FILE MANAGER - WRITE OPERATIONS
# ========================================

@app.route("/api/files/save", methods=["POST"])
@mutation_endpoint
def file_save():
    data = _json_body()

    result = save_text_file(
        _root(data),
        data.get("path"),
        data.get("content"),
    )

    _audit("save", data.get("path"))

    return jsonify({
        "ok": True,
        "item": result,
    })


@app.route("/api/files/permissions", methods=["POST"])
@mutation_endpoint
def file_permissions():
    data = _json_body()

    result = chmod_path(
        _root(data),
        data.get("path"),
        data.get("mode"),
    )

    _audit("chmod", data.get("path"))

    return jsonify({
        "ok": True,
        "item": result,
    })


@app.route("/api/files/create-file", methods=["POST"])
@mutation_endpoint
def file_create():
    data = _json_body()

    result = create_file(
        _root(data),
        data.get("parent_path", ""),
        data.get("name"),
        data.get("content", ""),
    )

    _audit(
        "create_file",
        None,
        result["path"],
    )

    return jsonify({
        "ok": True,
        "item": result,
    }), 201


@app.route("/api/files/create-folder", methods=["POST"])
@mutation_endpoint
def folder_create():
    data = _json_body()

    result = create_folder(
        _root(data),
        data.get("parent_path", ""),
        data.get("name"),
    )

    _audit(
        "create_folder",
        None,
        result["path"],
    )

    return jsonify({
        "ok": True,
        "item": result,
    }), 201


@app.route("/api/files/upload", methods=["POST"])
@mutation_endpoint
def file_upload():
    result = save_upload(
        request.form.get("root", "system"),
        request.form.get("parent_path", ""),
        request.files.get("file"),
    )

    _audit(
        "upload",
        None,
        result["path"],
    )

    return jsonify({
        "ok": True,
        "item": result,
    }), 201


@app.route("/api/files/rename", methods=["POST"])
@mutation_endpoint
def file_rename():
    data = _json_body()

    result = rename_path(
        _root(data),
        data.get("path"),
        data.get("new_name"),
    )

    _audit(
        "rename",
        data.get("path"),
        result["path"],
    )

    return jsonify({
        "ok": True,
        "item": result,
    })


@app.route("/api/files/copy", methods=["POST"])
@mutation_endpoint
def file_copy():
    data = _json_body()

    result = copy_path(
        _root(data),
        data.get("source_path"),
        data.get("destination_path"),
    )

    _audit(
        "copy",
        data.get("source_path"),
        result["path"],
    )

    return jsonify({
        "ok": True,
        "item": result,
    }), 201


@app.route("/api/files/move", methods=["POST"])
@mutation_endpoint
def file_move():
    data = _json_body()

    result = move_path(
        _root(data),
        data.get("source_path"),
        data.get("destination_path"),
    )

    _audit(
        "move",
        data.get("source_path"),
        result["path"],
    )

    return jsonify({
        "ok": True,
        "item": result,
    })


@app.route("/api/files/delete", methods=["POST"])
@mutation_endpoint
def file_delete():
    data = _json_body()

    delete_path(
        _root(data),
        data.get("path"),
        data.get("confirm"),
    )

    _audit(
        "delete",
        data.get("path"),
    )

    return jsonify({
        "ok": True
    })


# ========================================
# HEALTH CHECK
# ========================================

@app.route("/health")
def health():
    return jsonify({
        "status": "online",
        "application": "CloudOps Dashboard",
    })


# ========================================
# APPLICATION ENTRY POINT
# ========================================

if __name__ == "__main__":
    # Deliberately local-only. Do not run this process as root.
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
    )
