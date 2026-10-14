"""A fake QNAP NAS serving sanitised QuTS hero h5.2.10 responses."""

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

ROUTES = {
    "subfunc=sysinfo&hd=no": "sysinfo.xml",
    "subfunc=sysinfo&sysHealth=1": "sysinfo_health.xml",
    "chart_func=disk_usage": "disk_usage.xml",
    "qsmart.cgi?func=all_hd_data": "qsmart.xml",
    "extra_pool_index=1": "pool_index.xml",
    "Pool_Info=1&poolID=1": "pool_info_1.xml",
    "Pool_Info=1&poolID=2": "pool_info_2.xml",
    "extra_vol_index=1": "volume_index.xml",
    "chart_func=QSM40bandwidth": "bandwidth_qsm40.xml",
    "chart_func=bandwidth": "bandwidth.xml",
}

LOGIN_OK = (
    b'<?xml version="1.0" encoding="UTF-8"?><QDocRoot version="1.0">'
    b"<authPassed><![CDATA[1]]></authPassed><authSid><![CDATA[sid1]]></authSid>"
    b"</QDocRoot>"
)
AUTH_FAILED = (
    b'<?xml version="1.0" encoding="UTF-8"?><QDocRoot version="1.0">'
    b"<authPassed><![CDATA[0]]></authPassed></QDocRoot>"
)


class FakeResponse:
    """Just enough of requests.Response for qnapstats."""

    def __init__(self, content: bytes, content_type: str = "text/xml") -> None:
        self.status_code = 200
        self.content = content
        self.text = content.decode()
        self.headers = {"Content-Type": content_type}
        self.cookies = {}


class FakeSession:
    """A requests.Session stand-in bound to a FakeNas."""

    def __init__(self, nas: "FakeNas") -> None:
        self.nas = nas

    def get(self, url, timeout=None, verify=None):
        return self.nas.route(url)

    def post(self, url, data, timeout=None, verify=None):
        return self.nas.route(url)


class FakeNas:
    """Route requests to fixtures; can reject logins or expire the next request."""

    def __init__(self) -> None:
        self.login_ok = True
        self.expire_next = False
        self.overrides: dict[str, bytes] = {}
        self.requests: list[str] = []

    def session(self) -> FakeSession:
        return FakeSession(self)

    def route(self, url: str) -> FakeResponse:
        self.requests.append(url)
        if "authLogin.cgi" in url:
            return FakeResponse(LOGIN_OK if self.login_ok else AUTH_FAILED)
        if self.expire_next:
            self.expire_next = False
            return FakeResponse(AUTH_FAILED)
        for needle, body in self.overrides.items():
            if needle in url:
                return FakeResponse(body)
        for needle, fixture in ROUTES.items():
            if needle in url:
                return FakeResponse((FIXTURES / fixture).read_bytes())
        raise AssertionError(f"unrouted URL {url}")
