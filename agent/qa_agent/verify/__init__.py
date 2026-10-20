"""Deterministic oracles over recorded observations."""

from .models import Check, CheckReport
from .run import evaluate

__all__ = ['Check', 'CheckReport', 'evaluate']
