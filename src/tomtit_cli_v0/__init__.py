"""Product-facing, additive-only CLI facade for the frozen TOMTIT runner."""

from .cli import (
    CLIRequest,
    CLIResult,
    CLIValidationError,
    UnsupportedVerifierMode,
    VerifierMode,
    build_request,
    build_runtime_argv,
    execute,
    main,
)

__all__ = (
    "CLIRequest",
    "CLIResult",
    "CLIValidationError",
    "UnsupportedVerifierMode",
    "VerifierMode",
    "build_request",
    "build_runtime_argv",
    "execute",
    "main",
)
