"""Current request, so services can record who did what from where without passing request around."""
from contextvars import ContextVar

current_request: ContextVar = ContextVar("ccid4_request", default=None)
