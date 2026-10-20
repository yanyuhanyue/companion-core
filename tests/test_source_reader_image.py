"""Exercise Reader startup with only its image's allowed shared policy files."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def test_reader_help_needs_only_stdlib_source_policy_from_core(tmp_path):
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "source_reader", tmp_path / "source_reader")
    package = tmp_path / "companion"
    package.mkdir()
    for name in ("__init__.py", "source_policy.py"):
        source = root / "companion" / name
        if source.is_file():
            shutil.copy2(source, package / name)
    script = """
import importlib.abc
import runpy
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])

class ReaderBoundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'fastapi', 'fastembed', 'pydantic', 'alembic', 'sqlite3'}:
            raise ImportError('FORBIDDEN_READER_DEPENDENCY:' + fullname)
        if fullname.startswith('companion.') and fullname != 'companion.source_policy':
            raise ImportError('FORBIDDEN_CORE_MODULE:' + fullname)

sys.meta_path.insert(0, ReaderBoundary())
sys.argv = ['source_reader', '--help']
runpy.run_module('source_reader', run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(tmp_path)],
        cwd=tmp_path,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Bounded Bilibili / Heybox source reader" in result.stdout
