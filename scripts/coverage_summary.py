"""Render coverage.xml as a Markdown table for the CI job summary.

Lives in a script rather than inline in the workflow so it can be run and
debugged locally, and so YAML quoting never mangles Python.
"""

import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

REPORT = Path("coverage.xml")
GATE_PERCENT = 80.0


def main() -> int:
    """Print a Markdown coverage summary, or a note when there is no report."""
    if not REPORT.exists():
        print("## Coverage\n\nNo coverage report was produced.")
        return 0

    root = ElementTree.parse(REPORT).getroot()
    total = float(root.get("line-rate", "0")) * 100

    verdict = "pass" if total >= GATE_PERCENT else "FAIL"
    print("## Coverage\n")
    print(f"**{total:.1f}% line coverage** (gate: {GATE_PERCENT:.0f}% - {verdict})\n")
    print("| Package | Coverage |")
    print("| --- | ---: |")

    packages = sorted(root.iter("package"), key=lambda item: float(item.get("line-rate", "0")))
    for package in packages:
        name = package.get("name", "(root)")
        rate = float(package.get("line-rate", "0")) * 100
        print(f"| `{name}` | {rate:.1f}% |")

    return 0


if __name__ == "__main__":
    sys.exit(main())
