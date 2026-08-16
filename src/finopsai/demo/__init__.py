"""Demo data generation: simulated workloads and simulated infrastructure spend.

Everything in this package fabricates data so the dashboard has a story before
real cost sources are wired up. Simulated cost records are tagged
``raw["simulated"] = true`` and use a ``mock-`` dedup-key prefix so they can
always be told apart from measured spend.
"""
