"""Two complete, disjoint pytest shards, balanced by the suite's measured hot files."""

from pathlib import Path

import pytest

# Relative weights from the October 2026 suite. New files automatically join the
# lighter shard; changed test names and parametrization cannot drop coverage.
WEIGHTS = {
    "tests/test_onebot_capture.py": 80,
    "tests/test_live_continuity.py": 52,
    "tests/test_live_text_window.py": 27,
    "tests/test_ops_observe.py": 18,
    "tests/test_worker_composition.py": 17,
    "tests/test_onebot_window.py": 15,
    "tests/test_p8b_context_benchmark.py": 8,
}


def partition(files):
    shards, loads = [set(), set()], [0, 0]
    for name in sorted(set(files), key=lambda name: (-WEIGHTS.get(name, 2), name)):
        index = min(range(2), key=loads.__getitem__)
        shards[index].add(name)
        loads[index] += WEIGHTS.get(name, 2)
    return shards


def pytest_addoption(parser):
    parser.addoption("--ci-shard", type=int, choices=[0, 1], default=None)


def pytest_collection_modifyitems(config, items):
    index = config.getoption("--ci-shard")
    if index is None:
        return
    names = {item: Path(item.path).relative_to(config.rootpath).as_posix() for item in items}
    selected_files = partition(names.values())[index]
    selected = [item for item in items if names[item] in selected_files]
    if not selected:
        raise pytest.UsageError("CI shard collected no tests")
    deselected = [item for item in items if names[item] not in selected_files]
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected
