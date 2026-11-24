import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="session")
def cluster():
    """ShopLite's three services, started once for the whole test run (time compressed 10x)."""
    from shoplite.run import Cluster
    c = Cluster(time_scale=0.1).start()
    yield c
    c.stop()
