"""The Qnap constants."""

CONF_DRIVES = "drives"
CONF_NICS = "nics"
CONF_VOLUMES = "volumes"

DEFAULT_PORT = 8080
# ZFS pool queries on QuTS hero can take several seconds; core's 5 s is too tight.
DEFAULT_TIMEOUT = 20
DEFAULT_SSL = False
DEFAULT_VERIFY_SSL = True

DOMAIN = "qnap"
