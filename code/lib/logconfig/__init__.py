"""Logging configuration module.

Provides setup_logging() for entry point scripts and get_logger() for all modules.
"""

from .logconfig import setup_logging, get_logger

__all__ = ["setup_logging", "get_logger"]
