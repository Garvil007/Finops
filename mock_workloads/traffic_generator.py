"""Simulate three teams issuing tagged LLM calls through the LiteLLM proxy.

Thin CLI shim. The logic lives in :mod:`finopsai.demo.traffic` so it is covered
by ``mypy src/`` and the test suite::

    python mock_workloads/traffic_generator.py --count 200 --rate 5
    python mock_workloads/traffic_generator.py --mode live --max-spend-usd 0.25
    python mock_workloads/traffic_generator.py --burst research/experiment-planner
"""

from finopsai.demo.traffic import main

if __name__ == "__main__":
    main()
