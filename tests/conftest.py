import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLIP = ROOT / "tests/data/benchmark_1080p.mp4"


def pytest_addoption(parser):
    parser.addoption("--run-matrix", action="store_true",
                     help="re-run the full 1,056-case byte-identity matrix (~13 minutes)")


def pytest_configure(config):
    config.addinivalue_line("markers", "matrix: full byte-identity matrix; needs --run-matrix")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-matrix"):
        return
    skip = pytest.mark.skip(reason="needs --run-matrix (the recorded evidence is checked instead)")
    for item in items:
        if "matrix" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def clip():
    if not CLIP.exists():
        subprocess.run([sys.executable, str(ROOT / "scripts/generate_benchmark_assets.py")], check=True)
    return CLIP


@pytest.fixture
def out_path(tmp_path):
    """A path for an artifact that must not exist yet (the engine never overwrites)."""
    return tmp_path / "out.tenzor"
