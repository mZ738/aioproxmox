"""Constant definitions."""

DEFAULT_PVE_PORT = 8006
# How long a host gets to prove it is there. Short on purpose: it is
# asked on the error path, where every second is added to a request
# that has already waited out its own timeout.
PROBE_TIMEOUT = 10.0
