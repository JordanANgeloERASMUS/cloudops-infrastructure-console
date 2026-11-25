import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import file_manager
import monitoring
import process_manager
import service_manager
from app import app


class WindowsCompatibilityTests(unittest.TestCase):
    def test_windows_disk_partitions_include_each_volume(self):
        partitions = [
            SimpleNamespace(
                device="C:\\",
                mountpoint="C:\\",
                fstype="NTFS",
                opts="rw,fixed",
            ),
            SimpleNamespace(
                device="D:\\",
                mountpoint="D:\\",
                fstype="NTFS",
                opts="rw,fixed",
            ),
            SimpleNamespace(
                device="E:\\",
                mountpoint="E:\\",
                fstype="UDF",
                opts="cdrom",
            ),
        ]
        with (
            patch("monitoring.platform.system", return_value="Windows"),
            patch(
                "monitoring.psutil.disk_partitions",
                return_value=partitions,
            ),
        ):
            result = monitoring.get_unique_disk_partitions()

        self.assertEqual(
            [partition.mountpoint for partition in result],
            ["C:\\", "D:\\"],
        )

    def test_disk_alerts_do_not_duplicate_the_primary_windows_volume(self):
        alerts = monitoring.build_dashboard_alerts({
            "cpu_usage": 10,
            "memory_usage": 20,
            "disk_usage": 96,
            "disk_mountpoint": "C:\\",
            "disk_partitions": [
                {"mountpoint": "C:\\", "usage_percent": 96},
                {"mountpoint": "D:\\", "usage_percent": 96},
            ],
        })

        self.assertEqual(
            [alert["id"] for alert in alerts],
            ["filesystem-root:critical", "filesystem:D:\\:critical"],
        )

    def test_windows_metrics_use_the_primary_drive_and_report_all_volumes(self):
        partitions = [
            SimpleNamespace(
                device="C:\\",
                mountpoint="C:\\",
                fstype="NTFS",
                opts="rw,fixed",
            ),
            SimpleNamespace(
                device="D:\\",
                mountpoint="D:\\",
                fstype="NTFS",
                opts="rw,fixed",
            ),
        ]
        disk_usage = {
            "C:\\": SimpleNamespace(
                total=1000, used=600, free=400, percent=60
            ),
            "D:\\": SimpleNamespace(
                total=2000, used=1000, free=1000, percent=50
            ),
        }
        memory = SimpleNamespace(
            percent=40, total=1000, used=400, available=600
        )
        network = SimpleNamespace(bytes_sent=123, bytes_recv=456)

        with (
            patch("monitoring.platform.system", return_value="Windows"),
            patch("monitoring._primary_disk_path", return_value="C:\\"),
            patch("monitoring.psutil.cpu_percent", return_value=25),
            patch("monitoring.psutil.virtual_memory", return_value=memory),
            patch("monitoring.psutil.disk_partitions", return_value=partitions),
            patch(
                "monitoring.psutil.disk_usage",
                side_effect=lambda path: disk_usage[path],
            ),
            patch("monitoring.psutil.net_io_counters", return_value=network),
        ):
            result = monitoring.get_system_metrics(
                include_processes=False,
                cpu_interval=None,
            )

        self.assertEqual(result["disk_mountpoint"], "C:\\")
        self.assertEqual(result["disk_usage"], 60)
        self.assertEqual(
            [item["mountpoint"] for item in result["disk_partitions"]],
            ["C:\\", "D:\\"],
        )

    def test_windows_storage_root_is_the_current_user_profile(self):
        with (
            patch("file_manager.IS_WINDOWS", True),
            patch("file_manager.FILESYSTEM_ROOT", Path.home()),
        ):
            roots = file_manager.get_allowed_storage_roots()

        self.assertEqual(roots[0]["name"], "User Files")
        self.assertEqual(Path(roots[0]["path"]), Path.home())

    def test_windows_rejects_reserved_file_names_and_unix_chmod(self):
        with patch("file_manager.IS_WINDOWS", True):
            with self.assertRaises(file_manager.FileManagerError):
                file_manager._validate_name("CON.txt")
            with self.assertRaises(file_manager.FileManagerError):
                file_manager.chmod_path("system", "anything.txt", "644")

    def test_process_termination_is_disabled_on_windows(self):
        with patch("process_manager.os.name", "nt"):
            with self.assertRaisesRegex(PermissionError, "unavailable on Windows"):
                process_manager.terminate_process(10, 0)

    def test_systemd_manager_reports_unsupported_platform(self):
        with patch("service_manager.platform.system", return_value="Windows"):
            with self.assertRaisesRegex(RuntimeError, "Linux only"):
                service_manager.get_services()

    def test_windows_homepage_explains_acl_and_hides_process_termination_grant(self):
        client = app.test_client()
        with (
            patch("app.platform.system", return_value="Windows"),
            patch(
                "app.get_allowed_storage_roots",
                return_value=[{
                    "id": "system",
                    "name": "User Files",
                    "path": "C:\\Users\\Example",
                }],
            ),
        ):
            response = client.get("/")

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Windows security settings", html)
        self.assertIn("C:\\\\Users\\\\Example", html)
        self.assertNotIn('id="access-control-processes"', html)

    def test_windows_domain_qualified_process_owner_is_matched_safely(self):
        with (
            patch("process_manager.os.name", "nt"),
            patch("process_manager.CURRENT_USER", "operator"),
            patch.dict("process_manager.os.environ", {"USERDOMAIN": "OPS"}),
        ):
            self.assertTrue(process_manager._is_current_user("OPS\\operator"))
            self.assertFalse(process_manager._is_current_user("OTHER\\operator"))


if __name__ == "__main__":
    unittest.main()
