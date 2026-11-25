import re
import unittest
from unittest.mock import patch

from app import app
from monitoring import build_dashboard_alerts
from operations_console import CONSOLE_ACTIONS, run_diagnostic


def grant_permissions(client, **overrides):
    permissions = {
        "monitoring": True,
        "read_files": True,
        "write_files": True,
        "control_processes": True,
        "remote_access": True,
        "share_files": True,
    }
    permissions.update(overrides)
    with client.session_transaction() as session:
        session["access_configured"] = True
        session["access_permissions"] = permissions


class DashboardAlertTests(unittest.TestCase):
    def test_alerts_include_threshold_and_mountpoint_context(self):
        alerts = build_dashboard_alerts({
            "cpu_usage": 85,
            "memory_usage": 95,
            "disk_usage": 70,
            "disk_partitions": [
                {"mountpoint": "/", "usage_percent": 99},
                {"mountpoint": "/data", "usage_percent": 96},
            ],
        })

        by_id = {alert["id"]: alert for alert in alerts}
        self.assertEqual(
            set(by_id),
            {"cpu:warning", "memory:critical", "filesystem:/data:critical"},
        )
        self.assertIn("85.0%", by_id["cpu:warning"]["message"])
        self.assertIn("95%", by_id["memory:critical"]["message"])
        self.assertIn("/data", by_id["filesystem:/data:critical"]["title"])

    def test_healthy_metrics_produce_no_alerts(self):
        self.assertEqual(
            build_dashboard_alerts({
                "cpu_usage": 20,
                "memory_usage": 45,
                "disk_usage": 72,
                "disk_partitions": [],
            }),
            [],
        )


class OperationsConsoleTests(unittest.TestCase):
    def test_only_fixed_diagnostics_are_exposed(self):
        self.assertEqual(
            set(CONSOLE_ACTIONS),
            {"system", "storage", "network", "processes"},
        )
        with self.assertRaises(ValueError):
            run_diagnostic("echo arbitrary command")

    def test_diagnostic_endpoint_is_read_only_and_rejects_unknown_actions(self):
        client = app.test_client()
        grant_permissions(client)

        actions = client.get("/api/console/actions")
        self.assertEqual(actions.status_code, 200)
        self.assertEqual(
            {action["id"] for action in actions.json["actions"]},
            set(CONSOLE_ACTIONS),
        )
        for action in CONSOLE_ACTIONS:
            with self.subTest(action=action):
                response = client.get(f"/api/console/{action}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json["action"], action)
                self.assertIn("result", response.json)

        self.assertEqual(
            client.get("/api/console/unknown").status_code,
            404,
        )
        self.assertEqual(
            client.post("/api/console/system").status_code,
            405,
        )

    def test_metrics_endpoint_includes_derived_alerts(self):
        client = app.test_client()
        grant_permissions(client, read_files=False, write_files=False)
        with patch("app.get_system_metrics", return_value={
            "cpu_usage": 96,
            "memory_usage": 20,
            "disk_usage": 50,
            "disk_partitions": [],
        }) as get_metrics:
            response = client.get("/api/metrics")

        self.assertEqual(response.status_code, 200)
        get_metrics.assert_called_once_with(
            include_processes=False,
            cpu_interval=None,
        )
        self.assertEqual(
            [alert["id"] for alert in response.json["alerts"]],
            ["cpu:critical"],
        )


class AccessPermissionTests(unittest.TestCase):
    def test_scoped_apis_require_their_matching_grants(self):
        client = app.test_client()
        self.assertEqual(client.get("/api/metrics").status_code, 403)
        self.assertEqual(client.get("/api/storage/browse").status_code, 403)

        grant_permissions(
            client,
            read_files=False,
            write_files=False,
            control_processes=False,
            remote_access=False,
            share_files=False,
        )
        self.assertEqual(client.get("/api/metrics").status_code, 200)
        self.assertEqual(client.get("/api/storage/browse").status_code, 403)
        self.assertEqual(
            client.post(
                "/api/files/create-file",
                json={"name": "blocked.txt"},
            ).status_code,
            403,
        )

    def test_user_can_grant_and_revoke_scopes_with_csrf_protection(self):
        client = app.test_client()
        page = client.get("/")
        token = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            page.get_data(as_text=True),
        ).group(1)
        response = client.post(
            "/api/permissions",
            json={
                "monitoring": True,
                "read_files": True,
                "write_files": False,
                "control_processes": False,
                "remote_access": False,
                "share_files": False,
            },
            headers={
                "Origin": "http://localhost",
                "X-CSRF-Token": token,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get("/api/metrics").status_code, 200)
        self.assertEqual(client.get("/api/storage/browse").status_code, 200)
        self.assertEqual(
            client.post(
                "/api/files/create-file",
                json={"name": "blocked.txt"},
                headers={
                    "Origin": "http://localhost",
                    "X-CSRF-Token": token,
                },
            ).status_code,
            403,
        )

    def test_write_scope_requires_read_scope(self):
        client = app.test_client()
        page = client.get("/")
        token = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            page.get_data(as_text=True),
        ).group(1)
        response = client.post(
            "/api/permissions",
            json={
                "monitoring": False,
                "read_files": False,
                "write_files": True,
                "control_processes": False,
                "remote_access": False,
                "share_files": False,
            },
            headers={
                "Origin": "http://localhost",
                "X-CSRF-Token": token,
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_file_sharing_scope_requires_remote_device_access(self):
        client = app.test_client()
        page = client.get("/")
        token = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            page.get_data(as_text=True),
        ).group(1)
        response = client.post(
            "/api/permissions",
            json={
                "monitoring": False,
                "read_files": False,
                "write_files": False,
                "control_processes": False,
                "remote_access": False,
                "share_files": True,
            },
            headers={
                "Origin": "http://localhost",
                "X-CSRF-Token": token,
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_file_changes_still_require_csrf_after_write_access_is_granted(self):
        client = app.test_client()
        grant_permissions(client)

        with patch("app.create_file") as create_file:
            response = client.post(
                "/api/files/create-file",
                json={
                    "parent_path": "/tmp",
                    "name": "must-not-be-created.txt",
                },
            )

        self.assertEqual(response.status_code, 403)
        create_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
