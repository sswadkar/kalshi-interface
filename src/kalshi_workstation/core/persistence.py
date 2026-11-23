"""Atomic replacement for small, single-process JSON configuration files."""
import json
import logging
import os
from pathlib import Path
import tempfile

logger = logging.getLogger(__name__)


class ConfigurationSaveError(Exception):
    """The previous configuration file is intact; the new one was not saved."""


def write_json_atomic(path: Path, data: dict) -> None:
    """Write beside the destination, then replace it without exposing partial JSON.

    The directory must already exist. This does not coordinate multiple processes
    or persist exchange orders; the application supports one worker.
    """
    temporary = None
    try:
        payload = json.dumps(data, indent=2, allow_nan=False) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError) as exc:
        logger.exception("Could not save %s", path.name)
        raise ConfigurationSaveError(
            f"Could not save {path.name}. The previous saved configuration is unchanged. "
            "Check that the data directory exists, is writable, and has free space."
        ) from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove temporary configuration file", exc_info=True)
