"""Shared domain errors."""


class QuantLabError(Exception):
    """Base class for expected QuantLab failures."""


class ContractError(QuantLabError):
    """Raised when an input violates a versioned contract."""


class ChronologyError(QuantLabError):
    """Raised when ordered market events move backwards in time."""
