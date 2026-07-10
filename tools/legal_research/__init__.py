"""Adilet (adilet.zan.kz) legal research client for the lawyer role."""

from .adilet_client import (
    AdiletClient,
    AdiletError,
    AdiletNetworkError,
    AdiletParseError,
    FetchResult,
)

__all__ = ["AdiletClient", "AdiletError", "AdiletNetworkError", "AdiletParseError", "FetchResult"]
