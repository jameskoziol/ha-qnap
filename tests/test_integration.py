"""Home Assistant tests: config flow and entities against the fake QuTS hero NAS."""

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.qnap.const import DOMAIN

USER_INPUT = {
    "host": "nas.example",
    "username": "monitor",
    "password": "pw",
    "ssl": True,
    "verify_ssl": False,
    "port": 443,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/qnap."""
    return


@pytest.fixture
async def entry(hass: HomeAssistant, nas) -> MockConfigEntry:
    """A set-up config entry with every entity enabled."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="Q000EXAMPLE", title="NAS-Example", data=USER_INPUT
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if ent.disabled_by:
            registry.async_update_entity(ent.entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _state(hass: HomeAssistant, entity_id: str):
    state = hass.states.get(entity_id)
    assert state is not None, (
        f"{entity_id} missing; have: {sorted(hass.states.async_entity_ids())}"
    )
    return state


async def test_user_flow(hass: HomeAssistant, nas) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "NAS-Example"
    assert result["result"].unique_id == "Q000EXAMPLE"


async def test_user_flow_invalid_auth(hass: HomeAssistant, nas) -> None:
    nas.login_ok = False
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_fans_and_power_supplies(hass: HomeAssistant, entry) -> None:
    assert entry.state is ConfigEntryState.LOADED
    assert _state(hass, "sensor.nas_example_system_fan_1_speed").state == "7756"
    assert _state(hass, "sensor.nas_example_system_fan_4_speed").state == "7592"
    assert _state(hass, "binary_sensor.nas_example_system_fan_1").state == STATE_OFF
    assert _state(hass, "sensor.nas_example_power_supply_2_fan_speed").state == "4440"
    assert _state(hass, "sensor.nas_example_power_supply_1_temperature").state == "40"
    assert _state(hass, "binary_sensor.nas_example_power_supply_1").state == STATE_OFF


async def test_storage_pools(hass: HomeAssistant, entry) -> None:
    used = _state(hass, "sensor.nas_example_storage_pool_2_used")
    assert float(used.state) == pytest.approx(63.99, abs=0.01)
    assert used.attributes["Alert Threshold"] == 80
    assert used.attributes["Type"] == "hdd"
    free = _state(hass, "sensor.nas_example_storage_pool_2_free_space")
    assert free.attributes["unit_of_measurement"] == "TiB"
    assert float(free.state) == pytest.approx(47397390587546 / 2**40, abs=0.01)
    assert _state(hass, "binary_sensor.nas_example_storage_pool_2").state == STATE_OFF
    assert (
        _state(hass, "binary_sensor.nas_example_storage_pool_2_over_threshold").state
        == STATE_OFF
    )


async def test_thin_volume_is_bounded_by_its_pool(hass: HomeAssistant, entry) -> None:
    """Core reports ~77 TB free and 49% used for this share; the pool has 43 TB."""
    free = _state(hass, "sensor.nas_example_free_space_library")
    assert float(free.state) == pytest.approx(47397390587546 / 2**30, rel=1e-3)
    assert free.attributes["Storage Pool"] == "2"
    used_pct = float(_state(hass, "sensor.nas_example_volume_used_library").state)
    used = 75138251046400
    assert used_pct == pytest.approx(used / (used + 47397390587546) * 100, abs=0.1)


async def test_volume_sizes_have_unique_names(hass: HomeAssistant, entry) -> None:
    """Core names every volume total 'Volume size', so entity IDs collide."""
    assert _state(hass, "sensor.nas_example_volume_size_library")
    assert _state(hass, "sensor.nas_example_volume_size_archive")


async def test_expired_session_keeps_entities_available(
    hass: HomeAssistant, entry, nas
) -> None:
    nas.expire_next = True
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
    await hass.async_block_till_done()
    assert _state(hass, "sensor.nas_example_cpu_usage").state != STATE_UNAVAILABLE
    assert _state(hass, "sensor.nas_example_system_fan_1_speed").state == "7756"


async def test_nas_unreachable_marks_unavailable(
    hass: HomeAssistant, entry, nas
) -> None:
    nas.login_ok = False
    nas.expire_next = True
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
    await hass.async_block_till_done()
    assert _state(hass, "sensor.nas_example_cpu_usage").state == STATE_UNAVAILABLE


async def test_diagnostics_redacts_identity(hass: HomeAssistant, entry) -> None:
    from custom_components.qnap.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diag)
    assert "Q000EXAMPLE" not in text
    assert "nas.example" not in text
    assert "00:00:5e:00:53" not in text
    assert diag["data"]["pools"]["2"]["capacity"] == 152832116260864
