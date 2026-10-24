import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

from scripts.ci_shards import SHARD_COUNT, measured_weights, partition

ROOT = Path(__file__).resolve().parents[1]


def test_partition_is_complete_disjoint_and_deterministic_for_new_files():
    files = [path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").glob("test_*.py")]
    files += ["tests/test_new_runtime.py"]
    shards = partition(files)
    assert len(shards) == SHARD_COUNT == 4
    assert all(shards) and set.union(*shards) == set(files)
    assert sum(map(len, shards)) == len(set(files))
    assert partition(reversed(files)) == shards


def test_updated_weights_balance_slow_files_without_splitting_modules(monkeypatch):
    monkeypatch.setattr("scripts.ci_shards.WEIGHTS", {
        "tests/test_slow.py": 40, "tests/test_medium.py": 30,
        "tests/test_other.py": 20, "tests/test_last.py": 10,
    })
    shards = partition(["tests/test_slow.py", "tests/test_medium.py", "tests/test_other.py",
                        "tests/test_last.py", "tests/test_new.py"])
    assert shards == [{"tests/test_slow.py"}, {"tests/test_medium.py"},
                      {"tests/test_other.py"}, {"tests/test_last.py", "tests/test_new.py"}]


def test_cli_shards_execute_every_parametrized_case_exactly_once(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    for name in ("alpha", "beta", "new", "fourth", "fifth"):
        (tmp_path / f"test_{name}.py").write_text(
            "import pytest\n@pytest.mark.parametrize('value', [1, 2])\n"
            "def test_case(value):\n    assert value > 0\n", encoding="utf-8",
        )
    cases = []
    for index in range(SHARD_COUNT):
        report = tmp_path / f"shard-{index}.xml"
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "scripts.ci_shards", "--ci-shard", str(index),
             "-q", "--basetemp=" + str(tmp_path / f"tmp-{index}"),
             "-o", "cache_dir=" + str(tmp_path / "cache"), "--junitxml=" + str(report)],
            cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        cases.extend((case.attrib["classname"], case.attrib["name"])
                     for case in ElementTree.parse(report).iter("testcase"))
    assert len(cases) == len(set(cases)) == 10


def test_junit_refresh_keeps_total_fixture_time_and_max_across_platforms(tmp_path):
    reports = []
    for index, seconds in enumerate((5, 9)):
        report = tmp_path / f"report-{index}.xml"
        report.write_text(
            '<testsuites><testsuite>'
            f'<testcase classname="tests.test_example.TestClass" time="{seconds}"/>'
            '<testcase classname="tests.test_example" time="2"/>'
            '<testcase classname="tests.test_another" time="0"><skipped/></testcase>'
            '</testsuite></testsuites>', encoding="utf-8",
        )
        reports.append(report)
    assert measured_weights(reports) == {
        "tests/test_another.py": 0, "tests/test_example.py": 11,
    }
    assert measured_weights(reports + reports) == measured_weights(reports)


@pytest.mark.parametrize("body", [
    '', '<testcase classname="tests.test_example" time="nan"/>',
    '<testcase classname="tests.test_example" time="-1"/>',
])
def test_junit_refresh_rejects_empty_or_invalid_timings(tmp_path, body):
    report = tmp_path / "bad.xml"
    report.write_text(f"<testsuite>{body}</testsuite>", encoding="utf-8")
    with pytest.raises(ValueError):
        measured_weights([report])
