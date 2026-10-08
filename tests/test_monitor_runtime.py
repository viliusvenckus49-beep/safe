import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location(
    "monitor_runtime", Path(__file__).parents[1] / "deploy/monitor_runtime.py"
)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def test_monitor_retains_only_aggregates_and_never_raw_private_output():
    raw = json.dumps(
        {
            "healthy": False,
            "services": {"bot": "unhealthy", "postgres": "healthy", "token": "PRIVATE"},
            "metrics": {"ban_terminal": 3, "user_id": 42},
            "username": "PRIVATE",
        }
    )
    result = monitor.aggregate(raw)
    assert result == {
        "healthy": False,
        "services": {"bot": "unhealthy", "postgres": "healthy"},
        "metrics": {"ban_terminal": 3},
    }
    assert "PRIVATE" not in str(result) and "user_id" not in str(result)
    assert monitor.aggregate("secret traceback PRIVATE") == {"healthy": False, "maintenance": False}
    assert monitor.aggregate('{"healthy":true,"services":{"bot":[]}}')["services"] == {}
    assert monitor.aggregate("SAFECheck maintenance in progress")["maintenance"]


def test_private_history_records_sequential_health_samples(tmp_path):
    path = tmp_path / "private" / "history.jsonl"
    monitor.record_history(path, {"healthy": True, "metrics": {"ban_terminal": 0}})
    monitor.record_history(path, {"healthy": False, "metrics": {"ban_terminal": 1}})
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["healthy"] for row in rows] == [True, False]
    assert all("timestamp" in row for row in rows)
    assert path.stat().st_mode & 0o777 == 0o600


def test_monitor_follows_active_release_without_restarting_any_services(tmp_path, monkeypatch):
    release = tmp_path / "release"
    (release / "deploy").mkdir(parents=True)
    (release / "deploy/ops.py").touch()
    calls, recorded = [], []

    def execute(command, **kwargs):
        calls.append(command)
        if command[0] == "docker":
            return SimpleNamespace(stdout=str(release).encode(), returncode=0)
        return SimpleNamespace(
            stdout=b'{"healthy":true,"services":{"bot":"healthy"},"metrics":{}}', returncode=0
        )

    monkeypatch.setattr(monitor.subprocess, "run", execute)
    monkeypatch.setattr(monitor, "record_history", lambda path, result: recorded.append(result))
    monkeypatch.setattr(monitor.sys, "argv", ["monitor_runtime.py"])
    assert monitor.run() == 0
    assert calls[1][1] == str(release / "deploy/ops.py") and calls[1][-1] == "monitor"
    assert not any("restart" in command or "restore" in command for command in calls)
    assert recorded[0]["healthy"]
