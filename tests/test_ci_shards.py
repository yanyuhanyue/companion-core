import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

from scripts.ci_shards import partition

ROOT = Path(__file__).resolve().parents[1]


def test_partition_is_complete_disjoint_and_deterministic_for_new_files():
    files = [path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").glob("test_*.py")]
    files += ["tests/test_new_runtime.py"]
    first, second = partition(files)
    assert first and second and not first & second
    assert first | second == set(files)
    assert partition(reversed(files)) == [first, second]
    assert "tests/test_onebot_capture.py" in first
    assert "tests/test_live_continuity.py" in second


def test_cli_shards_execute_every_parametrized_case_exactly_once(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    for name in ("alpha", "beta", "new"):
        (tmp_path / f"test_{name}.py").write_text(
            "import pytest\n@pytest.mark.parametrize('value', [1, 2])\n"
            "def test_case(value):\n    assert value > 0\n", encoding="utf-8",
        )
    cases = []
    for index in range(2):
        report = tmp_path / f"shard-{index}.xml"
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "scripts.ci_shards", "--ci-shard", str(index),
             "-q", "--basetemp=" + str(tmp_path / f"tmp-{index}"),
             "-o", "cache_dir=" + str(tmp_path / "cache"), "--junitxml=" + str(report)],
            cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        cases.extend((case.attrib["classname"], case.attrib["name"])
                     for case in ElementTree.parse(report).iter("testcase"))
    assert len(cases) == len(set(cases)) == 6
