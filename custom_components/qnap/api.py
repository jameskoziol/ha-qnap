"""QNAP API client: qnapstats 0.4.0 plus QuTS hero (ZFS) support and fixes.

Each change here is shaped so it can go upstream to python-qnapstats as its own PR:
the session-retry fix, tolerant system-stats parsing, fan and PSU readings, and
ZFS storage-pool info.
"""

from typing import Any

from qnapstats import QNAPStats


class QnapAuthError(Exception):
    """The NAS rejected the credentials, or the session could not be created."""


def _int(value: Any) -> int | None:
    """Parse an integer the API may send as text, blank or missing."""
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _float(value: Any) -> float | None:
    """Parse a float the API may send as text, blank or missing."""
    if value is None:
        return None
    try:
        return float(str(value).strip().rstrip("%"))
    except ValueError:
        return None


class QnapClient(QNAPStats):
    """qnapstats with the fixes and QuTS hero endpoints the integration needs."""

    def _get_url(self, url, retry_on_error=True, **kwargs):
        """Make a GET request, retrying once on a dead session.

        qnapstats 0.4.0 retries but discards the retry's result, so every expired
        session costs one failed poll. It also carries on after a failed login and
        dies building a debug string from the missing session ID (a TypeError).
        """
        self._init_session()
        if self._session_error:
            raise QnapAuthError("Login to the NAS failed")

        result = self._execute_get_url(url, **kwargs)
        if (self._session_error or result is None) and retry_on_error:
            return self._get_url(url, False, **kwargs)

        return result

    def _handle_response(self, resp, force_list=None):
        """Accept 'text/xml; charset=…' and a missing Content-Type header."""
        if resp.status_code == 200 and not resp.headers.get(
            "Content-Type", ""
        ).startswith("text/xml"):
            return None
        resp.headers["Content-Type"] = "text/xml"
        return super()._handle_response(resp, force_list=force_list)

    def get_system_stats(self) -> dict[str, Any] | None:
        """Obtain system information, fans and power supplies in one request.

        Same shape as qnapstats' get_system_stats, plus "sysfans" (upstream 0.6.0
        shape) and "psus". Fields a model doesn't report come back as None rather
        than raising KeyError.
        """
        resp = self._get_url(
            "management/manaRequest.cgi?subfunc=sysinfo&hd=no&multicpu=1",
            force_list=("DNS_LIST"),
        )
        if resp is None:
            return None

        root = resp["func"]["ownContent"]["root"]
        cpu_usage = root.get("cpu_usage")

        details: dict[str, Any] = {
            "system": {
                "name": root.get("server_name"),
                "model": resp["model"]["displayModelName"],
                "serial_number": root.get("serial_number"),
                "temp_c": _int(root.get("sys_tempc")),
                "temp_f": _int(root.get("sys_tempf")),
                "timezone": root.get("timezone"),
            },
            "firmware": {
                "name": resp["firmware"].get("name"),
                "version": resp["firmware"]["version"],
                "number": resp["firmware"].get("number"),
                "build": resp["firmware"]["build"],
                "patch": resp["firmware"]["patch"],
                "build_time": resp["firmware"]["buildTime"],
            },
            "uptime": {
                "days": _int(root.get("uptime_day")) or 0,
                "hours": _int(root.get("uptime_hour")) or 0,
                "minutes": _int(root.get("uptime_min")) or 0,
                "seconds": _int(root.get("uptime_sec")) or 0,
            },
            "cpu": {
                "model": root.get("cpu_model"),
                "usage_percent": _float(cpu_usage),
                "temp_c": _int(root.get("cpu_tempc")),
                "temp_f": _int(root.get("cpu_tempf")),
            },
            "memory": {
                "total": _float(root.get("total_memory")),
                "free": _float(root.get("free_memory")),
            },
            "thresholds": {
                "cpu_temp_warn": _int(root.get("CPUTempWarnT")),
                "cpu_temp_error": _int(root.get("CPUTempErrT")),
                "system_temp_warn": _int(root.get("SysTempWarnT")),
                "system_temp_error": _int(root.get("SysTempErrT")),
                "hdd_temp_warn": _int(root.get("HDTempWarnT")),
                "hdd_temp_error": _int(root.get("HDTempErrT")),
                "ssd_temp_warn": _int(root.get("SSDTempWarnT")),
                "ssd_temp_error": _int(root.get("SSDTempErrT")),
            },
            "nics": {},
            "dns": [],
            "sysfans": {},
            "psus": {},
        }

        for nic_index in range(_int(root.get("nic_cnt")) or 0):
            i = str(nic_index + 1)
            if "eth_status" + i not in root:
                continue
            details["nics"]["eth" + str(nic_index)] = {
                "link_status": "Up" if root["eth_status" + i] == "1" else "Down",
                "max_speed": _int(root.get("eth_max_speed" + i)),
                "ip": root.get("eth_ip" + i),
                "mask": root.get("eth_mask" + i),
                "mac": root.get("eth_mac" + i),
                "usage": root.get("eth_usage" + i),
                "rx_packets": _int(root.get("rx_packet" + i)),
                "tx_packets": _int(root.get("tx_packet" + i)),
                "err_packets": _int(root.get("err_packet" + i)),
            }

        if dns_info := root.get("dnsInfo"):
            details["dns"].extend(dns_info.get("DNS_LIST") or [])

        # System fans. *_stat is -1 on alert; sysfan_fail is 1 on failure.
        for fan_index in range(_int(root.get("sysfan_count")) or 0):
            i = str(fan_index + 1)
            speed = _int(root.get("sysfan" + i))
            if speed is None:
                continue
            failed = _int(root.get("sysfan" + i + "_stat")) == -1 or bool(
                _int(root.get("sysfan_fail" + i))
            )
            details["sysfans"]["sysfan" + str(fan_index)] = {
                "speed": speed,
                "status": "alert" if failed else "ok",
            }

        # Power supplies, decoded the way QTS's System Status page does:
        # status >= 0 is OK, -1 is failed (or removed, when temperature and fan are
        # also -1), -2/-3 means the slot isn't present. Fan RPM 0 is a stopped fan.
        psu_index = 1
        while (status := _int(root.get(f"Power{psu_index}Status"))) is not None:
            temp = _int(root.get(f"Power{psu_index}Temp"))
            fan = _int(root.get(f"PowerFanStatus{psu_index}"))
            if status >= -1:
                removed = status == -1 and temp == -1 and fan == -1
                details["psus"][f"psu{psu_index}"] = {
                    "status": "ok"
                    if status >= 0
                    else ("removed" if removed else "failed"),
                    "temp_c": temp if temp is not None and temp >= 0 else None,
                    "fan_speed": fan if fan is not None and fan >= 0 else None,
                }
            psu_index += 1

        return details

    def get_pool_ids(self) -> list[str]:
        """List storage pool IDs (QuTS hero / storage v2)."""
        resp = self._get_url(
            "disk/disk_manage.cgi?func=extra_get&extra_pool_index=1",
            force_list=("row",),
        )
        if resp is None or not resp.get("Pool_Index"):
            return []
        return [
            row["poolID"]
            for row in resp["Pool_Index"].get("row") or []
            # IDs from 288 up are static volumes, not pools (QTS storage manager)
            if row.get("poolID") is not None and (_int(row["poolID"]) or 0) < 288
        ]

    def get_pool(self, pool_id: str) -> dict[str, Any] | None:
        """Obtain capacity, usage and health of one storage pool."""
        resp = self._get_url(
            f"disk/disk_manage.cgi?func=extra_get&Pool_Info=1&poolID={pool_id}",
            force_list=("row",),
        )
        if resp is None or _int(resp.get("result")) != 0 or not resp.get("Pool_Index"):
            return None
        rows = resp["Pool_Index"].get("row") or []
        if not rows:
            return None
        row = rows[0]

        capacity = _int(row.get("capacity_bytes"))
        allocated = _int(row.get("allocated_bytes"))
        original = _int(row.get("data_size_original"))
        after_dedup = _int(row.get("data_size_after_dedup"))
        return {
            "id": pool_id,
            "status": _int(row.get("pool_status")),
            "is_ssd": row.get("is_ssd_pool") == "1",
            "capacity": capacity,
            "allocated": allocated,
            "free": _int(row.get("freesize_bytes")),
            "used_percent": (
                allocated / capacity * 100
                if capacity and allocated is not None
                else None
            ),
            "threshold_percent": _int(row.get("pool_threshold")),
            "over_threshold": row.get("pool_over_threshold") not in (None, "0"),
            "snapshot_reserved": _int(row.get("snapshot_reserved_bytes")),
            "snapshot_used": _int(row.get("snapshot_reserved_used_bytes")),
            "data_saved": (
                original - after_dedup
                if original is not None and after_dedup is not None
                else None
            ),
            "dedup_saved": _int(row.get("dedup_saving")),
        }

    def get_pools(self) -> dict[str, dict[str, Any]]:
        """Obtain every storage pool, keyed by pool ID. Empty on non-ZFS models."""
        pools = {}
        for pool_id in self.get_pool_ids():
            if (pool := self.get_pool(pool_id)) is not None:
                pools[pool_id] = pool
        return pools

    def get_volume_pools(self) -> dict[str, str]:
        """Map volume ID to the pool it lives in."""
        resp = self._get_url(
            "disk/disk_manage.cgi?func=extra_get&extra_vol_index=1",
            force_list=("row",),
        )
        if resp is None or not resp.get("Volume_Index"):
            return {}
        return {
            row["vol_no"]: row["poolID"]
            for row in resp["Volume_Index"].get("row") or []
            if row.get("vol_no") is not None and row.get("poolID") is not None
        }
