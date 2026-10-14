"""Tests for the QNAP API client against sanitised QuTS hero h5.2.10 responses."""

import importlib.util
from pathlib import Path
import sys

import pytest

from tests.fakes import FIXTURES

ROOT = Path(__file__).parent.parent

# Load api.py on its own so these tests don't need Home Assistant installed.
_spec = importlib.util.spec_from_file_location(
    "qnap_api", ROOT / "custom_components" / "qnap" / "api.py"
)
api = importlib.util.module_from_spec(_spec)
sys.modules["qnap_api"] = api
_spec.loader.exec_module(api)


@pytest.fixture
def client(nas):
    """A client wired to the fake NAS."""
    return api.QnapClient("https://nas.example", 443, "monitor", "pw", timeout=20)


def test_system_stats_shape(client):
    stats = client.get_system_stats()
    assert stats["system"]["model"] == "TS-h1887XU-RP"
    assert stats["system"]["serial_number"] == "Q000EXAMPLE"
    assert stats["system"]["temp_c"] == 40
    assert stats["firmware"]["name"] == "QuTS hero"
    assert stats["firmware"]["version"] == "h5.2.10"
    assert stats["cpu"]["temp_c"] == 62
    assert stats["cpu"]["usage_percent"] == pytest.approx(12.8)
    assert set(stats["nics"]) == {f"eth{i}" for i in range(6)}
    assert stats["nics"]["eth4"]["link_status"] == "Up"
    assert stats["thresholds"]["hdd_temp_warn"] == 55
    assert stats["thresholds"]["cpu_temp_error"] == 80


def test_system_fans(client):
    fans = client.get_system_stats()["sysfans"]
    assert fans == {
        "sysfan0": {"speed": 7756, "status": "ok"},
        "sysfan1": {"speed": 7673, "status": "ok"},
        "sysfan2": {"speed": 7539, "status": "ok"},
        "sysfan3": {"speed": 7592, "status": "ok"},
    }


def test_power_supplies(client):
    psus = client.get_system_stats()["psus"]
    assert psus == {
        "psu1": {"status": "ok", "temp_c": 40, "fan_speed": 2700},
        "psu2": {"status": "ok", "temp_c": 40, "fan_speed": 4440},
    }


def test_missing_fields_do_not_raise(client, nas):
    """Models without a system sensor or fans (upstream #108/#110) still parse."""
    xml = (FIXTURES / "sysinfo.xml").read_text(encoding="utf-8")
    for tag in ("sys_tempc", "sys_tempf", "sysfan_count", "eth_status3"):
        xml = xml.replace(f"<{tag}>", f"<x_{tag}>").replace(f"</{tag}>", f"</x_{tag}>")
    nas.overrides["subfunc=sysinfo&hd=no"] = xml.encode()
    stats = client.get_system_stats()
    assert stats["system"]["temp_c"] is None
    assert stats["sysfans"] == {}
    assert "eth2" not in stats["nics"]


def test_pools(client):
    pools = client.get_pools()
    assert set(pools) == {"1", "2"}
    hdd = pools["2"]
    assert hdd["is_ssd"] is False
    assert hdd["status"] == 0
    assert hdd["capacity"] == 152832116260864
    assert hdd["allocated"] == 97793119860275
    assert hdd["free"] == 47397390587546
    assert hdd["used_percent"] == pytest.approx(63.99, abs=0.01)
    assert hdd["threshold_percent"] == 80
    assert hdd["over_threshold"] is False
    assert pools["1"]["is_ssd"] is True
    assert pools["1"]["data_saved"] == 803701145600 - 510500995072


def test_volume_pools(client):
    mapping = client.get_volume_pools()
    assert mapping["11"] == "2"  # Library (was 'Media') lives on the HDD pool
    assert mapping["5"] == "1"  # Container lives on the SSD pool


def test_thin_volume_free_is_pool_bound(client):
    """On ZFS the per-volume free size is the thin quota, not real pool space."""
    volumes = client.get_volumes()
    pools = client.get_pools()
    library = volumes["Library"]
    assert library["free_size"] > pools["2"]["free"]  # the bug being worked around


def test_expired_session_retry_returns_data(client, nas):
    """qnapstats 0.4.0 drops the retry's result after a session expiry."""
    client.get_system_health()
    nas.expire_next = True
    assert client.get_system_health() == "good"


def test_login_failure_raises_auth_error(client, nas):
    """qnapstats 0.4.0 raises TypeError here instead."""
    nas.login_ok = False
    with pytest.raises(api.QnapAuthError):
        client.get_system_stats()


def test_bandwidth_falls_back_on_quts_hero(client):
    bandwidth = client.get_bandwidth()
    assert "eth4" in bandwidth


@pytest.mark.parametrize("fixture", sorted(p.name for p in FIXTURES.glob("*.xml")))
def test_fixture_is_well_formed(fixture):
    """Sanitising must not break the XML."""
    import xml.dom.minidom

    xml.dom.minidom.parse(str(FIXTURES / fixture))


def test_smart_disk_health(client):
    disks = client.get_smart_disk_health()
    assert len(disks) == 18
    assert disks["0:1"]["type"] == "ssd"
    assert disks["0:7"]["type"] == "hdd"
    assert disks["0:7"]["health"] == "OK"
    assert disks["0:7"]["serial"] == "SERIAL0000"
