"""Keeps secrets that travel in a web address out of the request log. An invite link is
`/invite/<token>`: whoever reads the log (`journalctl -u totr`, a backup of it, a pasted excerpt)
could use a link nobody has opened yet. A join request is looked up by its secret id,
`/api/join/<id>`. The log still shows that the address was asked for."""

import logging
import re

SECRET_PATHS = re.compile(r"(/invite/|/api/join/)[^\s/?#\"']+")


def redact(text: str) -> str:
    return SECRET_PATHS.sub(r"\1[hidden]", text)


class Redact(logging.Filter):
    """A filter for uvicorn's access log, whose message is built from the address in its arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def log_config() -> dict:
    """uvicorn's own log settings with the filter on its access log."""
    import copy

    import uvicorn.config

    config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    config.setdefault("filters", {})["redact"] = {"()": "logredact.Redact"}
    config["handlers"]["access"]["filters"] = ["redact"]
    return config
