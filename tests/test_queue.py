"""The queue's shape, checked so the product-management rules don't depend on memory.

`docs/OPERATING_SYSTEM.md`, Product management: at most three items in progress, each naming the bet it tests; every
decision waiting on Thomas carries a default and a date; and no spec number is used twice. The brief checks what the
repo can't show (open PRs missing from `NOW.md`, idle PRs, next steps past their date).
"""

import re
from collections import Counter
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


def test_every_item_in_progress_names_the_bet_it_tests():
    for item in blocks(section("In progress"), r"\d+\. "):
        assert re.search(r"\bBets?:? (?:\d|none)", item), f"Name the bet it tests, or 'Bet: none': {item[:80]}"


def test_every_decision_waiting_has_a_default_and_a_date():
    for decision in blocks(section("Waiting on you"), r"- "):
        assert "Default:" in decision, f"No default: {decision[:80]}"
        assert re.search(rf"\bBy {DAY} \d{{1,2}} {MONTH}\b", decision), f"No date ('By Fri 13 Nov'): {decision[:80]}"


def test_spec_numbers_are_unique():
    specs = sorted(p.name for p in (ROOT / "docs" / "specs").glob("*.md") if p.name != "README.md")
    for name in specs:
        assert re.fullmatch(r"\d{3}-[a-z0-9-]+\.md", name), f"Spec names are NNN-short-name.md: {name}"
    counts = Counter(name[:3] for name in specs)
    clashes = sorted(number for number, n in counts.items() if n > 1)
    assert not clashes, f"Spec numbers used twice: {clashes}. Renumber the newer one before merging."
