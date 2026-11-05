"""Turn a pytest JUnit XML report into GitHub Actions error annotations.

Workflow logs need a GitHub login to read, but annotations appear on the run page and are readable
through the public checks API, so a failing job can be diagnosed without downloading logs.

usage: python scripts/annotate_junit.py pytest-report.xml
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_ERROR_ANNOTATIONS = 10  # GitHub shows at most 10 error annotations per step
MAX_DETAIL_CHARS = 3000


def escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(text: str) -> str:
    return escape_data(text).replace(":", "%3A").replace(",", "%2C")


def failures(report: Path) -> list[tuple[str, str, str]]:
    """(test id, kind, detail) for every failed or errored test case."""
    found = []
    for case in ET.parse(report).getroot().iter("testcase"):
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is not None:
                test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
                detail = f"{node.get('message', '')}\n{node.text or ''}".strip()
                found.append((test_id, kind, detail))
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    report = Path(argv[1])
    if not report.exists():
        message = f"{report} was not written; pytest may have crashed early"
        print(f"::error title=no test report::{message}")
        return 0

    found = failures(report)
    print(f"{len(found)} failing test(s)")
    shown = found[: MAX_ERROR_ANNOTATIONS - 1] if len(found) > MAX_ERROR_ANNOTATIONS else found
    for test_id, kind, detail in shown:
        title = escape_property(f"{kind}: {test_id}")[:200]
        print(f"::error title={title}::{escape_data(detail[:MAX_DETAIL_CHARS])}")
    rest = found[len(shown) :]
    if rest:
        names = "\n".join(f"{kind}: {test_id}" for test_id, kind, _ in rest[:60])
        print(f"::error title={len(rest)} more failing tests::{escape_data(names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
