"""
CloudOps Process Manager
------------------------
Reads Linux process information and provides guarded SIGTERM termination.

Safety:
- Does not execute shell commands.
- Does not run with elevated privileges.
- Only allows termination of processes owned by the current Linux user.
- Protects the CloudOps process and its parent process.
- Uses process creation time to reduce PID-reuse risks.
- Sends SIGTERM only; it never force-kills processes.
"""

import getpass
import os
import time

import psutil


CURRENT_USER = getpass.getuser()
PROTECTED_PIDS = {1, os.getpid(), os.getppid()}


def _is_current_user(process_user):
    if not process_user:
        return False

    process_user = process_user.casefold()
    current_user = CURRENT_USER.casefold()
    if process_user == current_user:
        return True

    domain = os.environ.get("USERDOMAIN", "").strip()
    if os.name == "nt" and domain and "\\" not in current_user:
        return process_user == f"{domain}\\{current_user}".casefold()

    return False


def _process_memory(process):
    """Return process RAM usage in bytes and percentage."""
    try:
        memory = process.memory_info()
        memory_percent = process.memory_percent()

        return {
            "memory_rss": memory.rss,
            "memory_percent": round(memory_percent, 2),
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return {
            "memory_rss": 0,
            "memory_percent": 0,
        }


def get_processes(sort_by="cpu"):
    """Return a snapshot of processes visible to the current user."""

    processes = []

    for process in psutil.process_iter(
        attrs=[
            "pid",
            "name",
            "username",
            "status",
            "create_time",
        ],
        ad_value=None,
    ):
        try:
            info = process.info
            memory = _process_memory(process)

            try:
                cpu_percent = process.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                cpu_percent = 0

            processes.append({
                "pid": info.get("pid"),
                "name": info.get("name") or "Unknown",
                "username": info.get("username") or "Unknown",
                "status": info.get("status") or "unknown",
                "cpu_percent": round(cpu_percent, 2),
                "memory_rss": memory["memory_rss"],
                "memory_percent": memory["memory_percent"],
                "create_time": info.get("create_time"),
            })

        except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
            continue

    if sort_by == "memory":
        processes.sort(
            key=lambda item: item["memory_rss"],
            reverse=True,
        )
    elif sort_by == "pid":
        processes.sort(
            key=lambda item: item["pid"] or 0,
        )
    elif sort_by == "name":
        processes.sort(
            key=lambda item: item["name"].lower(),
        )
    else:
        processes.sort(
            key=lambda item: item["cpu_percent"],
            reverse=True,
        )

    return {
        "processes": processes,
        "total": len(processes),
        "running": sum(
            1 for item in processes
            if item["status"] == psutil.STATUS_RUNNING
        ),
        "current_user": CURRENT_USER,
        "updated_at": time.time(),
    }


def get_process_details(pid):
    """Return additional information about one process."""

    try:
        process = psutil.Process(int(pid))

        with process.oneshot():
            memory = process.memory_info()
            cpu_times = process.cpu_times()

            try:
                executable = process.exe()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                executable = None

            try:
                working_directory = process.cwd()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                working_directory = None

            try:
                thread_count = process.num_threads()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                thread_count = None

            try:
                file_descriptors = process.num_fds()
            except (
                AttributeError,
                psutil.AccessDenied,
                psutil.NoSuchProcess,
            ):
                file_descriptors = None

            return {
                "pid": process.pid,
                "name": process.name(),
                "username": process.username(),
                "status": process.status(),
                "cpu_percent": round(process.cpu_percent(interval=None), 2),
                "memory_rss": memory.rss,
                "memory_percent": round(process.memory_percent(), 2),
                "executable": executable,
                "working_directory": working_directory,
                "threads": thread_count,
                "file_descriptors": file_descriptors,
                "cpu_user_seconds": round(cpu_times.user, 2),
                "cpu_system_seconds": round(cpu_times.system, 2),
                "create_time": process.create_time(),
            }

    except psutil.NoSuchProcess:
        raise ValueError("The requested process no longer exists.")

    except psutil.AccessDenied:
        raise PermissionError(
            "Your operating system denied access to information about this process."
        )

    except (TypeError, ValueError):
        raise ValueError("Invalid process ID.")


def terminate_process(pid, expected_create_time):
    """
    Request graceful termination of a process owned by the current user.

    expected_create_time must come from the process-list response.
    This helps protect against accidentally targeting a reused PID.
    """

    if os.name == "nt":
        raise PermissionError(
            "Process termination is unavailable on Windows. Use Task Manager "
            "for a process you understand."
        )

    try:
        pid = int(pid)
        expected_create_time = float(expected_create_time)
    except (TypeError, ValueError):
        raise ValueError("A valid PID and process creation time are required.")

    if pid <= 1:
        raise PermissionError("This system process is protected.")

    if pid in PROTECTED_PIDS:
        raise PermissionError(
            "CloudOps and its parent process are protected."
        )

    try:
        process = psutil.Process(pid)

        actual_create_time = process.create_time()

        if abs(actual_create_time - expected_create_time) > 0.01:
            raise ValueError(
                "The process changed since it was selected. Refresh the list."
            )

        process_user = process.username()

        if not _is_current_user(process_user):
            raise PermissionError(
                "You can only terminate processes owned by your current account."
            )

        process_name = process.name()

        # Send a graceful termination request. Do not force-kill.
        process.terminate()

        try:
            process.wait(timeout=3)
            terminated = True
            message = f"{process_name} (PID {pid}) terminated successfully."

        except psutil.TimeoutExpired:
            terminated = False
            message = (
                f"{process_name} (PID {pid}) has not exited yet. "
                "It may be finishing work or ignoring SIGTERM."
            )

        return {
            "pid": pid,
            "name": process_name,
            "terminated": terminated,
            "message": message,
        }

    except psutil.NoSuchProcess:
        raise ValueError("The process no longer exists.")

    except psutil.AccessDenied:
        raise PermissionError(
            "Your operating system denied permission to terminate this process."
        )