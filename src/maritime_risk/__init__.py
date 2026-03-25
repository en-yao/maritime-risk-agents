import os

import structlog

if os.environ.get("LOG_FORMAT", "") == "json":
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
    )
else:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
    )

if os.environ.get("DD_TRACE_ENABLED", "").lower() == "true":
    import ddtrace

    ddtrace.patch_all()
