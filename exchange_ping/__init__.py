"""exchange-ping: latency from this box to crypto exchange APIs (stdlib only)."""
__version__ = "0.1.0"

from .core import measure, server_time_ms  # noqa: E402,F401
from .discover import discover  # noqa: E402,F401
