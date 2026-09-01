"""Vendored logging configuration for the metadata-db loaders.

Provides `setup_logging()` for entry-point scripts (a JSON file handler
on the root logger, tagged with a per-run timestamp) and `get_logger()`
for every module. Vendored under code/lib/ so CI never depends on
untracked `.claude/` content; this copy intentionally forks from the
skill copy (skill updates do not propagate by design).
"""

import inspect
import logging
import os
from datetime import datetime
from pathlib import Path

from pythonjsonlogger.json import JsonFormatter


class RunTimestampFilter(logging.Filter):
    """Filter that adds a run_timestamp to each log record.

    Attached to the file handler (not a logger) so every record that
    reaches the file — from any module's logger — carries the timestamp
    of the run that produced it, letting one appended log file separate
    its runs.
    """

    def __init__(self, run_timestamp: str) -> None:
        """Store the run timestamp stamped onto every record.

        Args:
            run_timestamp: The run's start time, preformatted
                (`%Y.%m.%d_%H.%M.%S`).
        """
        super().__init__()
        self.run_timestamp = run_timestamp

    def filter(self, record: logging.LogRecord) -> bool:
        """Stamp the run timestamp onto a record; never drop it.

        Args:
            record: The log record passing through the handler.

        Returns:
            True always (the filter annotates, it does not filter).
        """
        record.run_timestamp = self.run_timestamp
        return True


def setup_logging(
    log_dir: str | Path,
    log_name: str | None = None,
    level: int = logging.DEBUG,
    overwrite: bool = True,
) -> None:
    """Configure logging for the application. Call ONCE from entry point script.

    Sets up a JSON file handler on the ROOT logger so all child loggers
    inherit it. A second call is a no-op (the existing handlers are kept),
    so libraries can never double-register the file handler.

    Args:
        log_dir: Directory path for the log file.
        log_name: Name of the log file (without extension). Defaults to
            the caller script's name.
        level: Logging level. Defaults to logging.DEBUG.
        overwrite: If True, overwrite the log file on each run. Defaults
            to True.
    """
    # Get the root logger
    root_logger = logging.getLogger()

    # Avoid adding duplicate handlers if already configured
    if not root_logger.handlers:
        root_logger.setLevel(level)
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        if log_name is None:
            # Default the log name to the caller's script (the frame that
            # invoked setup_logging). Every step of the frame chain is
            # optional (non-CPython implementations, interactive or
            # embedded callers), so guard each and fall back to "log".
            frame = inspect.currentframe()
            caller_filepath = (
                frame.f_back.f_globals.get("__file__")
                if frame and frame.f_back
                else None
            )
            if caller_filepath is None:
                log_name = "log"
            else:
                caller_script_name = os.path.basename(caller_filepath)
                log_name = (
                    caller_script_name.split(".")[0]
                    if "." in caller_script_name
                    else caller_script_name
                )
        log_filename = f"{log_name}.jsonl"

        run_timestamp = datetime.now().strftime("%Y.%m.%d_%H.%M.%S")
        timestamp_filter = RunTimestampFilter(run_timestamp)

        file_mode = "w" if overwrite else "a"
        handler = logging.FileHandler(
            log_path / log_filename, mode=file_mode, encoding="utf-8"
        )
        handler.addFilter(timestamp_filter)  # Add filter to HANDLER, not logger
        formatter = JsonFormatter(
            "%(run_timestamp)s %(asctime)s %(name)s %(funcName)s "
            "%(levelname)s %(message)s"
        )
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a module.

    This is a thin wrapper around logging.getLogger(). Use this in library
    modules that should not configure logging themselves.

    Args:
        name: Logger name (typically __name__ from the calling module).

    Returns:
        Logger instance.
    """
    return logging.getLogger(name)
