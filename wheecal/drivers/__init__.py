"""
VNA drivers, picked by name (config 'driver', CLI --driver, GUI selector).

To add a model, subclass base.VnaDriver in a new module here and add it to DRIVERS (docs/adding-a-vna.md).
"""
from __future__ import annotations

from .base import VnaDriver
from .rsa5065n import RSA5065N

DEFAULT = RSA5065N.NAME
DRIVERS: dict[str, type[VnaDriver]] = {RSA5065N.NAME: RSA5065N}


def get(name: str | None) -> type[VnaDriver]:
    key = (name or DEFAULT).strip().lower()
    try:
        return DRIVERS[key]
    except KeyError:
        raise KeyError(f"unknown VNA driver {name!r}; available: {', '.join(sorted(DRIVERS))}") from None


def names() -> list[str]:
    return sorted(DRIVERS)


def register(cls: type[VnaDriver]) -> type[VnaDriver]:
    """Add a driver class (usable as a decorator)."""
    if not cls.NAME:
        raise ValueError('a driver needs a NAME')
    DRIVERS[cls.NAME] = cls
    return cls
