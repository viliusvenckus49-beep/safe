import json
import os
import subprocess
import sys

import pytest

from app.test_ui.health import ready


@pytest.mark.parametrize("age,expected", [(0, True), (179, True), (181, False), (-1, False)])
def test_polling_probe_checks_fresh_heartbeat_and_design(tmp_path, age, expected):
    state = tmp_path / "design.json"
    state.write_text(json.dumps({"version": 1, "texts": {}, "layouts": {}}))
    heartbeat = tmp_path / "heartbeat"
    heartbeat.write_text("2000")
    os.utime(heartbeat, (2000 - age, 2000 - age))
    assert ready(state, current_time=2000) is expected


def test_missing_or_corrupt_state_and_heartbeat_are_unhealthy(tmp_path):
    state = tmp_path / "design.json"
    assert not ready(state)
    heartbeat = tmp_path / "heartbeat"
    heartbeat.write_text("2000")
    state.write_text("corrupt")
    assert not ready(state)


def test_health_probe_does_not_load_heavy_bot_modules():
    code = "import app.test_ui.health; import sys; assert 'aiogram' not in sys.modules; assert 'sqlalchemy' not in sys.modules; assert 'telethon' not in sys.modules"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True)
    assert result.returncode == 0
