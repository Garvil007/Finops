"""Command-line entrypoint dispatching to FinOpsAI services.

Subcommands (``api``, ``collectors``, ``alerts``, ``migrate``) are wired up in
a later phase; the module exists so the console script target resolves.
"""


def main() -> None:
    """Dispatch to a FinOpsAI service."""
    raise NotImplementedError("CLI dispatch is implemented in a later phase")
