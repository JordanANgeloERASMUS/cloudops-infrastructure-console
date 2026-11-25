from io import BytesIO
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from remote_agent import _configured_bind_address, create_agent_app
from remote_devices import (
    RemoteDeviceError,
    _DEVICES,
    _validate_host,
    add_device,
    get_device_metrics,
    list_devices,
    remove_device,
    send_file_to_device,
)


AGENT_TOKEN = "this-is-a-test-token-that-is-at-least-32-characters"
APPROVAL_TOKEN = "a-separate-local-approval-token-that-is-32-chars"
METRICS = {
    "cpu_usage": 12.5,
    "memory_usage": 41.0,
    "disk_usage": 63.0,
    "disk_mountpoint": "C:\\",
    "network": {"bytes_received": 1000, "bytes_sent": 2000},
    "uptime": {"formatted": "2d 3h 4m"},
    "system": {
        "hostname": "WIN-LAPTOP",
        "operating_system": "Windows",
        "architecture": "AMD64",
    },
}


class RemoteDeviceValidationTests(unittest.TestCase):
    def setUp(self):
        _DEVICES.clear()

    def tearDown(self):
        _DEVICES.clear()

    def test_only_tailscale_ipv4_addresses_are_accepted(self):
        self.assertEqual(_validate_host("100.101.2.3"), "100.101.2.3")
        for host in ("192.168.1.5", "127.0.0.1", "example.com", "::1"):
            with self.subTest(host=host), self.assertRaises(RemoteDeviceError):
                _validate_host(host)

    def test_pairing_authenticates_agent_and_never_returns_or_lists_key(self):
        responses = [
            {"application": "CloudOps Agent"},
            METRICS,
        ]
        with patch(
            "remote_devices._fetch_json",
            side_effect=responses,
        ) as fetch:
            paired = add_device("My laptop", "100.101.2.3", AGENT_TOKEN)

        self.assertEqual(paired["hostname"], "WIN-LAPTOP")
        self.assertEqual(paired["metrics"], METRICS)
        fetch.assert_any_call("100.101.2.3", AGENT_TOKEN, "/api/health")
        fetch.assert_any_call("100.101.2.3", AGENT_TOKEN, "/api/metrics")
        self.assertNotIn("token", paired)
        self.assertNotIn("token", list_devices()[0])

    def test_paired_metrics_require_same_device_identity(self):
        with patch(
            "remote_devices._fetch_json",
            side_effect=[
                {"application": "CloudOps Agent"},
                METRICS,
            ],
        ):
            paired = add_device("My laptop", "100.101.2.3", AGENT_TOKEN)

        with patch("remote_devices._fetch_json", return_value=METRICS):
            self.assertEqual(get_device_metrics(paired["id"]), METRICS)

        changed_identity = {**METRICS, "system": {"hostname": "OTHER-PC"}}
        with patch(
            "remote_devices._fetch_json",
            return_value=changed_identity,
        ):
            with self.assertRaisesRegex(RemoteDeviceError, "identity changed"):
                get_device_metrics(paired["id"])

    def test_sending_a_selected_file_targets_only_the_paired_agent(self):
        _DEVICES["device-1"] = {
            "id": "device-1",
            "name": "Laptop",
            "host": "100.101.2.3",
            "token": AGENT_TOKEN,
            "hostname": "WIN-LAPTOP",
            "operating_system": "Windows",
            "connected_at": 1,
            "last_seen": 1,
        }

        class FakeResponse:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return json.dumps({
                    "id": "share-1",
                    "filename": "notes.txt",
                    "size": 7,
                    "status": "awaiting_approval",
                }).encode()

        class FakeOpener:
            def __init__(self):
                self.request = None

            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return FakeResponse()

        opener = FakeOpener()
        with (
            patch(
                "remote_devices._fetch_json",
                side_effect=[
                    {"application": "CloudOps Agent"},
                    METRICS,
                ],
            ),
            patch(
                "remote_devices.urllib.request.build_opener",
                return_value=opener,
            ),
        ):
            result = send_file_to_device(
                "device-1",
                "notes.txt",
                b"payload",
            )

        self.assertEqual(result["status"], "awaiting_approval")
        self.assertEqual(opener.timeout, 30)
        self.assertEqual(opener.request.full_url, "http://100.101.2.3:8765/api/shares")
        self.assertEqual(
            opener.request.get_header("Authorization"),
            f"Bearer {AGENT_TOKEN}",
        )
        self.assertIn(b"filename=\"notes.txt\"", opener.request.data)
        self.assertIn(b"payload", opener.request.data)
        with self.assertRaises(RemoteDeviceError):
            send_file_to_device(
                "device-1",
                "../../CON.txt",
                b"payload",
            )

    def test_revocation_removes_credentials_from_device_registry(self):
        with patch(
            "remote_devices._fetch_json",
            side_effect=[
                {"application": "CloudOps Agent"},
                METRICS,
            ],
        ):
            paired = add_device("My laptop", "100.101.2.3", AGENT_TOKEN)

        remove_device(paired["id"])
        self.assertEqual(list_devices(), [])
        with self.assertRaises(RemoteDeviceError):
            get_device_metrics(paired["id"])

    def test_remote_agent_requires_key_and_exposes_only_read_only_get_routes(self):
        agent = create_agent_app(AGENT_TOKEN)
        client = agent.test_client()
        self.assertEqual(client.get("/api/health").status_code, 401)

        with patch("remote_agent.get_system_metrics", return_value=METRICS):
            response = client.get(
                "/api/metrics",
                headers={"Authorization": f"Bearer {AGENT_TOKEN}"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["system"]["hostname"], "WIN-LAPTOP")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(
            client.post(
                "/api/metrics",
                headers={"Authorization": f"Bearer {AGENT_TOKEN}"},
            ).status_code,
            405,
        )
        self.assertEqual(
            client.get(
                "/api/metrics",
                headers={"Authorization": "Bearer wrong-token"},
            ).status_code,
            401,
        )
        self.assertEqual(
            client.get(
                "/api/shell",
                headers={"Authorization": f"Bearer {AGENT_TOKEN}"},
            ).status_code,
            404,
        )

    def test_agent_bind_defaults_to_loopback_and_rejects_public_interfaces(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_configured_bind_address(), "127.0.0.1")

        for address in ("0.0.0.0", "192.168.1.4", "::"):
            with (
                self.subTest(address=address),
                patch.dict("os.environ", {"CLOUDOPS_AGENT_BIND": address}),
                self.assertRaises(SystemExit),
            ):
                _configured_bind_address()

        with patch.dict(
            "os.environ",
            {"CLOUDOPS_AGENT_BIND": "100.101.2.3"},
        ):
            self.assertEqual(_configured_bind_address(), "100.101.2.3")

    def test_dashboard_pairing_requires_permission_and_csrf_and_never_returns_token(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["access_configured"] = True
            session["access_permissions"] = {
                "monitoring": True,
                "read_files": False,
                "write_files": False,
                "control_processes": False,
                "remote_access": True,
                "share_files": False,
            }

        page = client.get("/")
        token = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            page.get_data(as_text=True),
        ).group(1)
        body = {"name": "Laptop", "host": "100.101.2.3", "token": AGENT_TOKEN}

        with patch("app.add_device", return_value={
            "id": "device-1",
            "name": "Laptop",
            "host": "100.101.2.3",
            "hostname": "WIN-LAPTOP",
            "operating_system": "Windows",
            "metrics": METRICS,
        }) as pair:
            denied = client.post("/api/remote/devices", json=body)
            paired = client.post(
                "/api/remote/devices",
                json=body,
                headers={
                    "Origin": "http://localhost",
                    "X-CSRF-Token": token,
                },
            )

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(paired.status_code, 201)
        self.assertNotIn("token", json.dumps(paired.json))
        pair.assert_called_once_with("Laptop", "100.101.2.3", AGENT_TOKEN)

    def test_remote_dashboard_api_is_disabled_without_remote_permission(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["access_configured"] = True
            session["access_permissions"] = {
                "monitoring": True,
                "read_files": False,
                "write_files": False,
                "control_processes": False,
                "remote_access": False,
                "share_files": False,
            }

        self.assertEqual(client.get("/api/remote/devices").status_code, 403)

    def test_remote_device_page_has_one_pairing_form_and_documents_scope(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["access_configured"] = True
            session["access_permissions"] = {
                "monitoring": False,
                "read_files": False,
                "write_files": False,
                "control_processes": False,
                "remote_access": True,
                "share_files": False,
            }

        html = client.get("/").get_data(as_text=True)
        self.assertEqual(html.count('id="remote-devices"'), 1)
        self.assertEqual(html.count('id="remote-pair-form"'), 1)
        self.assertIn("File sharing requires recipient approval", html)
        self.assertIn("tailscale ip -4", html)
        self.assertIn('id="remote-share-inbox"', html)
        self.assertIn('id="access-share-files"', html)

    def test_file_sharing_routes_require_grant_and_csrf(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["access_configured"] = True
            session["access_permissions"] = {
                "monitoring": False,
                "read_files": False,
                "write_files": False,
                "control_processes": False,
                "remote_access": True,
                "share_files": False,
            }

        self.assertEqual(client.get("/api/shares/pending").status_code, 403)
        with client.session_transaction() as session:
            session["access_permissions"] = {
                **session["access_permissions"],
                "share_files": True,
            }
        page = client.get("/")
        token = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            page.get_data(as_text=True),
        ).group(1)

        data = {"file": (BytesIO(b"payload"), "report.txt")}
        denied = client.post(
            "/api/remote/devices/device-1/shares",
            data=data,
            content_type="multipart/form-data",
        )
        self.assertEqual(denied.status_code, 403)

        with patch("app.send_file_to_device", return_value={
            "id": "request-1",
            "filename": "report.txt",
            "size": 7,
            "status": "awaiting_approval",
        }) as send:
            response = client.post(
                "/api/remote/devices/device-1/shares",
                data={"file": (BytesIO(b"payload"), "report.txt")},
                content_type="multipart/form-data",
                headers={
                    "Origin": "http://localhost",
                    "X-CSRF-Token": token,
                },
            )
        self.assertEqual(response.status_code, 202)
        send.assert_called_once()

    def test_file_offer_requires_agent_key_then_local_approval_before_save(self):
        agent = create_agent_app(AGENT_TOKEN, approval_token=APPROVAL_TOKEN)
        client = agent.test_client()
        offered = client.post(
            "/api/shares",
            data={
                "sender": "Linux workstation",
                "file": (BytesIO(b"approved contents"), "notes.txt"),
            },
            headers={"Authorization": f"Bearer {AGENT_TOKEN}"},
        )
        self.assertEqual(offered.status_code, 202)
        share_id = offered.json["id"]

        wrong_secret = client.get(
            "/api/shares/pending",
            headers={"Authorization": f"Bearer {AGENT_TOKEN}"},
        )
        self.assertEqual(wrong_secret.status_code, 401)
        pending = client.get(
            "/api/shares/pending",
            headers={"Authorization": f"Bearer {APPROVAL_TOKEN}"},
        )
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json["shares"][0]["filename"], "notes.txt")

        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch(
                "remote_agent.Path.home",
                return_value=Path(temporary_directory),
            ):
                saved = client.post(
                    f"/api/shares/{share_id}/decision",
                    json={"decision": "approve"},
                    headers={"Authorization": f"Bearer {APPROVAL_TOKEN}"},
                )
                destination = (
                    Path(temporary_directory)
                    / "CloudOps Shared"
                    / "Received"
                    / "notes.txt"
                )
                self.assertEqual(saved.status_code, 200)
                self.assertEqual(destination.read_bytes(), b"approved contents")
                self.assertEqual(
                    client.get(
                        "/api/shares/pending",
                        headers={"Authorization": f"Bearer {APPROVAL_TOKEN}"},
                    ).json["shares"],
                    [],
                )

    def test_rejected_offer_is_discarded_and_existing_files_are_never_overwritten(self):
        agent = create_agent_app(AGENT_TOKEN, approval_token=APPROVAL_TOKEN)
        client = agent.test_client()
        headers = {"Authorization": f"Bearer {AGENT_TOKEN}"}
        approval_headers = {"Authorization": f"Bearer {APPROVAL_TOKEN}"}

        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = (
                Path(temporary_directory) / "CloudOps Shared" / "Received"
            )
            destination.mkdir(parents=True)
            existing = destination / "notes.txt"
            existing.write_bytes(b"keep this")
            with patch(
                "remote_agent.Path.home",
                return_value=Path(temporary_directory),
            ):
                response = client.post(
                    "/api/shares",
                    data={
                        "sender": "Linux workstation",
                        "file": (BytesIO(b"new content"), "notes.txt"),
                    },
                    headers=headers,
                )
                share_id = response.json["id"]
                conflict = client.post(
                    f"/api/shares/{share_id}/decision",
                    json={"decision": "approve"},
                    headers=approval_headers,
                )
                self.assertEqual(conflict.status_code, 409)
                self.assertEqual(existing.read_bytes(), b"keep this")
                rejected = client.post(
                    f"/api/shares/{share_id}/decision",
                    json={"decision": "reject"},
                    headers=approval_headers,
                )
                self.assertEqual(rejected.status_code, 200)
                self.assertEqual(existing.read_bytes(), b"keep this")

    def test_file_offer_feature_is_unavailable_without_local_approval_secret(self):
        client = create_agent_app(AGENT_TOKEN).test_client()
        response = client.get(
            "/api/shares/pending",
            headers={"Authorization": f"Bearer {AGENT_TOKEN}"},
        )
        self.assertEqual(response.status_code, 403)

    def test_agent_and_local_approval_keys_must_be_distinct(self):
        with self.assertRaisesRegex(ValueError, "must be different"):
            create_agent_app(AGENT_TOKEN, approval_token=AGENT_TOKEN)


if __name__ == "__main__":
    unittest.main()
