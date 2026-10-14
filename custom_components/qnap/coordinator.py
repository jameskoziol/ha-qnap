"""Data coordinator for the qnap integration."""

from contextlib import contextmanager, nullcontext
from datetime import timedelta
import logging
from typing import Any, override
import warnings

from requests.exceptions import RequestException
import urllib3

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_TIMEOUT,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import QnapAuthError, QnapClient
from .const import DEFAULT_TIMEOUT, DOMAIN

type QnapConfigEntry = ConfigEntry[QnapCoordinator]

UPDATE_INTERVAL = timedelta(minutes=1)

_LOGGER = logging.getLogger(__name__)


@contextmanager
def suppress_insecure_request_warning():
    """Context manager to suppress InsecureRequestWarning.

    Was added in here to solve the following issue, not being solved upstream.
    https://github.com/colinodell/python-qnapstats/issues/96
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
        yield


def create_client(data: dict[str, Any]) -> QnapClient:
    """Build an API client from config entry data."""
    protocol = "https" if data[CONF_SSL] else "http"
    return QnapClient(
        f"{protocol}://{data[CONF_HOST]}",
        data[CONF_PORT],
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        verify_ssl=data[CONF_VERIFY_SSL],
        # Core never stores a timeout, so requests ran with none at all.
        timeout=data.get(CONF_TIMEOUT) or DEFAULT_TIMEOUT,
    )


class QnapCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Custom coordinator for the qnap integration."""

    config_entry: QnapConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: QnapConfigEntry) -> None:
        """Initialize the qnap coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self._verify_ssl = config_entry.data.get(CONF_VERIFY_SSL)
        self._api = create_client(dict(config_entry.data))

    def _sync_update(self) -> dict[str, Any]:
        """Get the latest data from the Qnap API."""
        with (
            suppress_insecure_request_warning()
            if not self._verify_ssl
            else nullcontext()
        ):
            system_stats = self._api.get_system_stats()
            if system_stats is None:
                raise UpdateFailed("The NAS returned no system information")
            return {
                "system_stats": system_stats,
                "system_health": self._api.get_system_health(),
                "smart_drive_health": self._api.get_smart_disk_health() or {},
                "volumes": self._api.get_volumes() or {},
                "bandwidth": self._api.get_bandwidth() or {},
                "pools": self._api.get_pools(),
                "volume_pools": self._api.get_volume_pools(),
            }

    @override
    async def _async_update_data(self) -> dict[str, Any]:
        """Get the latest data from the Qnap API."""
        try:
            return await self.hass.async_add_executor_job(self._sync_update)
        except QnapAuthError as err:
            raise UpdateFailed(f"Authentication with the NAS failed: {err}") from err
        except RequestException as err:
            raise UpdateFailed(f"Error communicating with the NAS: {err}") from err
