#!/usr/bin/env bash
# Sets up a Claude Code cloud session (started from Slack, the web or the phone) so the agent can test and score
# straight away. `.claude/settings.json` runs it at session start. Local sessions skip it.
# It never fails the session: anything it can't do, it reports, and the session carries on.
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

notes=()
pipi() { python3 -m pip install -q "$@" 2>/dev/null || python3 -m pip install -q --break-system-packages "$@" 2>/dev/null; }

pipi -e ".[dev]" || notes+=("pip install failed, so tests may not run")

# The test bed sits beside the product, as on the Mac mini.
if [ ! -d ../loopbench ]; then
  git clone -q https://github.com/flytonewyork/loopbench ../loopbench 2>/dev/null \
    || notes+=("couldn't clone ../loopbench, so CI's score job is the scoreboard")
fi
if [ -d ../loopbench ]; then
  pipi pandas numpy pyyaml scipy || notes+=("loopbench's dependencies didn't install")
fi

# What the session sees first.
echo "Cloud session ready. Start from NOW.md; the skills in .claude/skills are the protocol (ship for any build task)."
[ -d ../loopbench ] && echo "Scoreboard: cd ../loopbench && PYTHONPATH=src python scripts/scoreboard.py --seeds 1"
for n in "${notes[@]}"; do echo "Setup note: $n. Say so in the PR."; done
exit 0
