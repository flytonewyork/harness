"""The queue's shape, checked so the product-management rules don't depend on memory.

At most three items in progress, and every decision waiting on Thomas carries a default and a date.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOW = (ROOT / "NOW.md").read_text(encoding="utf-8")
DAY = r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"


def section(title):
    match = re.search(rf"^## {re.escape(title)}[^\n]*\n(.*?)(?=^## |\Z)", NOW, re.M | re.S)
    assert match, f"NOW.md has no '## {title}' section"
    return match.group(1)


def blocks(text, marker):
    """Each list item that starts with `marker` at the margin, joined with its indented continuation lines."""
    found = re.findall(rf"^{marker}.*?(?=^{marker}|^\S|\Z)", text, re.M | re.S)
    return [" ".join(block.split()) for block in found]


def test_at_most_three_items_in_progress():
    items = blocks(section("In progress"), r"\d+\. ")
    assert 1 <= len(items) <= 3, f"{len(items)} items in progress: the queue allows three at most"


def test_every_decision_waiting_has_a_default_and_a_date():
    for decision in blocks(section("Waiting on you"), r"- "):
        assert "Default:" in decision, f"No default: {decision[:80]}"
        assert re.search(rf"\bBy {DAY} \d{{1,2}} {MONTH}\b", decision), f"No date ('By Fri 13 Nov'): {decision[:80]}"

