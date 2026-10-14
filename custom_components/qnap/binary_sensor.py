"""Binary sensors for QNAP fan, power supply and storage pool faults."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import QnapConfigEntry, QnapCoordinator


@dataclass(frozen=True, kw_only=True)
class QnapBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a QNAP binary sensor over one item of a coordinator collection."""

    collection: Callable[[dict[str, Any]], dict[str, dict[str, Any]]]
    is_on: Callable[[dict[str, Any]], bool | None]
    placeholder: Callable[[str], str]
    attributes: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _fans(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return data["system_stats"]["sysfans"]


def _psus(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return data["system_stats"]["psus"]


def _pools(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return data["pools"]


BINARY_SENSORS: tuple[QnapBinarySensorEntityDescription, ...] = (
    QnapBinarySensorEntityDescription(
        key="fan_problem",
        translation_key="fan_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        collection=_fans,
        is_on=lambda fan: fan["status"] != "ok",
        placeholder=lambda key: str(int(key.removeprefix("sysfan")) + 1),
    ),
    QnapBinarySensorEntityDescription(
        key="psu_problem",
        translation_key="psu_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        collection=_psus,
        # A stopped PSU fan is shown red on the NAS's own status page.
        is_on=lambda psu: psu["status"] != "ok" or psu["fan_speed"] == 0,
        placeholder=lambda key: key.removeprefix("psu"),
        attributes=lambda psu: {"Status": psu["status"]},
    ),
    QnapBinarySensorEntityDescription(
        key="pool_problem",
        translation_key="pool_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        collection=_pools,
        # 0 is "Ready"; anything else is degraded, rebuilding or worse.
        is_on=lambda pool: None if pool["status"] is None else pool["status"] != 0,
        placeholder=lambda key: key,
        attributes=lambda pool: {"Status code": pool["status"]},
    ),
    QnapBinarySensorEntityDescription(
        key="pool_over_threshold",
        translation_key="pool_over_threshold",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        collection=_pools,
        is_on=lambda pool: pool["over_threshold"],
        placeholder=lambda key: key,
        attributes=lambda pool: {"Alert Threshold": pool["threshold_percent"]},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: QnapConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up entry."""
    coordinator = config_entry.runtime_data
    uid = config_entry.unique_id
    assert uid is not None
    async_add_entities(
        QNAPBinarySensor(coordinator, description, uid, item)
        for description in BINARY_SENSORS
        for item in description.collection(coordinator.data)
    )


class QNAPBinarySensor(CoordinatorEntity[QnapCoordinator], BinarySensorEntity):
    """A QNAP fault sensor for one fan, power supply or storage pool."""

    _attr_has_entity_name = True
    entity_description: QnapBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: QnapCoordinator,
        description: QnapBinarySensorEntityDescription,
        unique_id: str,
        monitor_device: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self.monitor_device = monitor_device
        self._attr_unique_id = f"{unique_id}_{description.key}_{monitor_device}"
        self._attr_translation_placeholders = {
            "monitor_device": description.placeholder(monitor_device)
        }
        system_stats = coordinator.data["system_stats"]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, unique_id)},
            serial_number=unique_id,
            name=system_stats["system"]["name"],
            model=system_stats["system"]["model"],
            sw_version=system_stats["firmware"]["version"],
            manufacturer="QNAP",
        )

    @property
    def _item(self) -> dict[str, Any] | None:
        return self.entity_description.collection(self.coordinator.data).get(
            self.monitor_device
        )

    @property
    @override
    def available(self) -> bool:
        """Return if the item is still reported."""
        return super().available and self._item is not None

    @property
    @override
    def is_on(self) -> bool | None:
        """Return true if the item has a fault."""
        if (item := self._item) is None:
            return None
        return self.entity_description.is_on(item)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the raw status behind the fault flag."""
        if (item := self._item) is None or self.entity_description.attributes is None:
            return None
        return self.entity_description.attributes(item)
