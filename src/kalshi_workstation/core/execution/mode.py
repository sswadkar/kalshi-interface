"""Shared dry-run switch for manual and arbitrage orders. Extensions keep their own."""
from dataclasses import dataclass


@dataclass
class ExecutionMode:
    dry_run: bool = True
