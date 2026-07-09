"""
Configuration layer for Kettu Mem.

Settings model with pydantic-settings.
All magic numbers and tunables live here.

Usage:
  from config import settings
  print(settings.data_dir)
  print(settings.port)
"""

from config.settings import Settings, settings

__all__ = ["Settings", "settings"]
