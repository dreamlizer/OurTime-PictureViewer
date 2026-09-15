"""Windows/local lifecycle tests for M1.  Never targets production data or port."""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "validation" / "work"
PYTHON = sys.executable
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def env_for(data: Path, port: int) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        PHOTO_LIBRARY_DATA=str(data),
        PHOTO_LIBRARY_PORT=str(port),
        PHOTO_MODEL_ROOT=str(data / "no-model"),
        PHOTO_GEO_ROOT=str(data / "no-geo"),
        PHOTO_NO_BROWSER="1",
        NO_ALBUMENTATIONS_UPDATE="1",
        PYTHONIOENCODING="utf-8",
    )
    return env


def health(port: int, timeout: float = 1.0) -> dict:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/api/health", timeout=timeout
    ) as response:
        return json.load(response)

def api(port: int, path: str, body=None, method: str | None = None) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def wait_health(port: int, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            return health(port)
        except (OSError, urllib.error.URLError) as exc:
            last = exc
            time.sleep(0.1)
    raise AssertionError(f"isolated server did not become healthy: {last}")


def start_server(data: Path, port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [PYTHON, str(ROOT / "app.py"), "--port", str(port)],
        cwd=ROOT,
        env=env_for(data, port),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )


def graceful_stop(process: subprocess.Popen, port: int) -> None:
    if process.poll() is not None:
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
        return
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/shutdown", method="POST", data=b""
    )
    try:
        urllib.request.urlopen(request, timeout=3).read()
    except OSError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    if process.stdout:
        process.stdout.close()
    if process.stderr:
        process.stderr.close()


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        WORK.mkdir(parents=True, exist_ok=True)

    def test_same_data_same_or_different_port_has_one_owner(self) -> None:
        with tempfile.TemporaryDirectory(dir=WORK) as parent:
            data = Path(parent) / "data"
            first_port, second_port = free_port(), free_port()
            first = start_server(data, first_port)
            try:
                wait_health(first_port)
                second = start_server(data / ".", second_port)
                out, err = second.communicate(timeout=15)
                self.assertNotEqual(0, second.returncode, out + err)
                self.assertIn("另一个拾光实例", out + err)
                self.assertEqual(str(data.resolve()), health(first_port)["data_dir"])
            finally:
                graceful_stop(first, first_port)

    def test_different_data_directories_can_run_together(self) -> None:
        with tempfile.TemporaryDirectory(dir=WORK) as parent:
            port_a, port_b = free_port(), free_port()
            data_a, data_b = Path(parent) / "a", Path(parent) / "b"
            first, second = start_server(data_a, port_a), start_server(data_b, port_b)
            try:
                self.assertEqual(str(data_a.resolve()), wait_health(port_a)["data_dir"])
                self.assertEqual(str(data_b.resolve()), wait_health(port_b)["data_dir"])
            finally:
                graceful_stop(first, port_a)
                graceful_stop(second, port_b)

    def test_crash_releases_os_lock_and_stale_pid_does_not_kill_unrelated(self) -> None:
        with tempfile.TemporaryDirectory(dir=WORK) as parent:
            data, first_port = Path(parent) / "data", free_port()
            first = start_server(data, first_port)
            wait_health(first_port)
            connection = sqlite3.connect(data / "library.sqlite3")
            try:
                connection.execute(
                    """INSERT INTO assets(
                         id,sha256,metadata,created_at,face_state
                       ) VALUES (1,?,'{}',datetime('now'),1)""",
                    ("a" * 64,),
                )
                connection.execute(
                    """INSERT INTO jobs(
                         id,roots,with_faces,include_system,status,started_at
                       ) VALUES ('interrupted','[]',0,0,'running',datetime('now'))"""
                )
                connection.commit()
            finally:
                connection.close()
            first.kill()
            first.communicate(timeout=5)
            (data / "server.pid").write_text(str(os.getpid()), encoding="ascii")
            second_port = free_port()
            second = start_server(data, second_port)
            try:
                self.assertTrue(wait_health(second_port)["owner"])
                connection = sqlite3.connect(data / "library.sqlite3")
                try:
                    job_status = connection.execute(
                        "SELECT status FROM jobs WHERE id='interrupted'"
                    ).fetchone()[0]
                    face_state = connection.execute(
                        "SELECT face_state FROM assets WHERE id=1"
                    ).fetchone()[0]
                finally:
                    connection.close()
                self.assertEqual("paused", job_status)
                self.assertEqual(1, face_state)
            finally:
                graceful_stop(second, second_port)

    def test_initialization_failure_releases_owner_for_next_start(self) -> None:
        with tempfile.TemporaryDirectory(dir=WORK) as parent:
            data = Path(parent) / "data"
            code = r"""
import json,app
original=app.init_db
def broken(): raise RuntimeError('injected migration failure')
app.init_db=broken
failed=False
try: app.initialize_application()
except RuntimeError: failed=True
released=not app.APP_OWNER or not app.APP_OWNER.handle
app.init_db=original
app.initialize_application()
second=bool(app.APP_OWNER and app.APP_OWNER.handle)
stopped=app.shutdown_application()
print(json.dumps({'failed':failed,'released':released,'second':second,'stopped':stopped}))
"""
            completed = subprocess.run(
                [PYTHON, "-c", code],
                cwd=ROOT,
                env=env_for(data, free_port()),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=30,
                check=True,
            )
            result = json.loads(completed.stdout.strip().splitlines()[-1])
            self.assertEqual(
                {"failed": True, "released": True, "second": True, "stopped": True},
                result,
            )

    def test_legacy_owner_detection_and_shutdown_timeout_are_conservative(self) -> None:
        with tempfile.TemporaryDirectory(dir=WORK) as parent:
            data = Path(parent) / "data"
            code = r"""
import json,os,pathlib,app
app.DATA.mkdir(parents=True,exist_ok=True)
(app.DATA/'server.pid').write_text('424242',encoding='ascii')
app.process_command_line=lambda pid: str((app.BASE/'app.py').resolve())
legacy=False
try: app.refuse_unlocked_legacy_owner()
except RuntimeError: legacy=True
(app.DATA/'server.pid').unlink()
app.initialize_application()
class Busy:
    def is_alive(self): return True
    def join(self,timeout): pass
app.SCAN_THREAD=Busy()
stopped=app.shutdown_application(.01)
held=bool(app.APP_OWNER and app.APP_OWNER.handle)
app.SCAN_THREAD=None
final=app.shutdown_application()
print(json.dumps({'legacy':legacy,'stopped':stopped,'held':held,'final':final}))
"""
            completed = subprocess.run(
                [PYTHON, "-c", code],
                cwd=ROOT,
                env=env_for(data, free_port()),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=30,
                check=True,
            )
            result = json.loads(completed.stdout.strip().splitlines()[-1])
            self.assertEqual(
                {"legacy": True, "stopped": False, "held": True, "final": True},
                result,
            )

    def test_runtime_version_is_fixed_for_process_lifetime(self) -> None:
        with tempfile.TemporaryDirectory(dir=WORK) as parent:
            data, port = Path(parent) / "data", free_port()
            process = start_server(data, port)
            try:
                first = wait_health(port)
                second = health(port)
                self.assertTrue(first["runtime_version"])
                self.assertEqual(first["runtime_version"], second["runtime_version"])
                self.assertEqual(first["pid"], second["pid"])
            finally:
                graceful_stop(process, port)

    def test_operation_receipt_survives_controlled_restart(self) -> None:
        with tempfile.TemporaryDirectory(dir=WORK) as parent:
            data, first_port = Path(parent) / "data", free_port()
            first = start_server(data, first_port)
            try:
                wait_health(first_port)
                connection = sqlite3.connect(data / "library.sqlite3")
                try:
                    connection.execute(
                        """INSERT INTO assets(
                             id,sha256,metadata,created_at,face_state
                           ) VALUES (1,?,'{}',datetime('now'),0)""",
                        ("b" * 64,),
                    )
                    connection.execute(
                        """INSERT INTO files(
                             asset_id,path,size,mtime_ns,modified_at,exists_now,excluded
                           ) VALUES (1,?,1,1,datetime('now'),1,0)""",
                        (str(Path(parent) / "fixture.jpg"),),
                    )
                    connection.commit()
                finally:
                    connection.close()
                operation_id = str(uuid.uuid4())
                committed = api(
                    first_port,
                    "/api/exclusions/assets",
                    {
                        "ids": [1],
                        "excluded": True,
                        "display_only": True,
                        "operation_id": operation_id,
                    },
                    "POST",
                )
                self.assertTrue(committed["state_committed"])
            finally:
                graceful_stop(first, first_port)
            second_port = free_port()
            second = start_server(data, second_port)
            try:
                wait_health(second_port)
                receipt = api(second_port, f"/api/operations/{operation_id}")
                self.assertEqual(operation_id, receipt["operation_id"])
                self.assertEqual("committed", receipt["status"])
                self.assertTrue(receipt["state_committed"])
            finally:
                graceful_stop(second, second_port)

    @unittest.skipUnless(os.name == "nt", "PowerShell launcher contract is Windows-only")
    def test_start_and_stop_scripts_use_isolated_configuration(self) -> None:
        with tempfile.TemporaryDirectory(dir=WORK) as parent:
            data, port = Path(parent) / "script-data", free_port()
            env = env_for(data, port)
            started = subprocess.run(
                ["powershell", "-NoProfile", "-File", str(ROOT / "start.ps1")],
                cwd=ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=40,
            )
            self.assertEqual(0, started.returncode)
            current = wait_health(port)
            self.assertEqual(str(data.resolve()), current["data_dir"])
            self.assertTrue((data / "server.pid").is_file())
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/app.js", method="HEAD"
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(
                    "no-cache, no-store, must-revalidate",
                    response.headers.get("Cache-Control"),
                )
            stopped = subprocess.run(
                ["powershell", "-NoProfile", "-File", str(ROOT / "stop.ps1")],
                cwd=ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=45,
            )
            self.assertEqual(0, stopped.returncode)
            self.assertFalse((data / "server.pid").exists())
            with self.assertRaises((OSError, urllib.error.URLError)):
                health(port, timeout=0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
