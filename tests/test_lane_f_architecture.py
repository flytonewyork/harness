"""Machine-enforceable architectural governance checks for `lane-f-core-engine`.

Protects `backend/{engine,case,extract,journeys,precare,api}/` against regressions into:
- internal module/package circular dependencies (e.g., `engine.rules <-> engine.escalation`),
- duplicated `norm()` or `claude -p` subprocess runners,
- reaching into `backend.gateway.gateway` internals instead of the public `backend.gateway` API,
- `backend/` importing `loopbench`,
- asymmetric-authority violations in `backend/case/model.py` or `backend/api/router.py`,
- composition-root bloat in `backend/api/app.py`.
"""
from __future__ import annotations

import ast
import collections
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
LANE_F_PACKAGES = ("engine", "case", "extract", "journeys", "precare", "api")


def _lane_f_modules() -> dict[str, Path]:
    mods: dict[str, Path] = {}
    for pkg in LANE_F_PACKAGES:
        pkg_dir = BACKEND / pkg
        if not pkg_dir.exists():
            continue
        for path in sorted(pkg_dir.rglob("*.py")):
            rel = path.relative_to(BACKEND).with_suffix("")
            parts = list(rel.parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            mods[".".join(["backend"] + parts)] = path
    return mods


def _find_cycles(nodes: set[str], edges: dict[str, set[str]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    state: dict[str, int] = {}
    stack: list[str] = []

    def dfs(u: str) -> None:
        state[u] = 1
        stack.append(u)
        for v in sorted(edges.get(u, ())):
            if state.get(v, 0) == 1:
                cycles.append(stack[stack.index(v) :] + [v])
            elif state.get(v, 0) == 0:
                dfs(v)
        stack.pop()
        state[u] = 2

    for n in sorted(nodes):
        if state.get(n, 0) == 0:
            dfs(n)
    return cycles


def test_lane_f_zero_internal_cycles():
    """Zero module-level or package-level circular imports across lane-f core packages."""
    mods = _lane_f_modules()
    mod_edges: dict[str, set[str]] = collections.defaultdict(set)
    pkg_edges: dict[str, set[str]] = collections.defaultdict(set)

    for mod, path in mods.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        is_pkg = path.name == "__init__.py"
        base_parts = mod.split(".") if is_pkg else mod.split(".")[:-1]
        src_pkg = mod.split(".")[1]

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in mods and alias.name != mod:
                        mod_edges[mod].add(alias.name)
                    if alias.name.startswith("backend."):
                        dst_pkg = alias.name.split(".")[1]
                        if dst_pkg in LANE_F_PACKAGES and dst_pkg != src_pkg:
                            pkg_edges[src_pkg].add(dst_pkg)
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    up = node.level - 1
                    tbase = base_parts[: len(base_parts) - up] if up else base_parts
                    target_prefix = ".".join(tbase + ([node.module] if node.module else []))
                else:
                    target_prefix = node.module or ""

                if not target_prefix.startswith("backend"):
                    continue

                if node.module is None:
                    for alias in node.names:
                        cand = f"{target_prefix}.{alias.name}"
                        if cand in mods and cand != mod:
                            mod_edges[mod].add(cand)
                        parts = cand.split(".")
                        if len(parts) > 1 and parts[1] in LANE_F_PACKAGES and parts[1] != src_pkg:
                            pkg_edges[src_pkg].add(parts[1])
                else:
                    if target_prefix in mods and target_prefix != mod:
                        mod_edges[mod].add(target_prefix)
                    parts = target_prefix.split(".")
                    if len(parts) > 1 and parts[1] in LANE_F_PACKAGES and parts[1] != src_pkg:
                        pkg_edges[src_pkg].add(parts[1])

    assert _find_cycles(set(LANE_F_PACKAGES), pkg_edges) == [], f"Lane-F package cycles: {pkg_edges}"
    assert _find_cycles(set(mods.keys()), mod_edges) == [], f"Lane-F module cycles detected"


def test_lane_f_single_source_of_truth():
    """Enforce single norm(), single claude -p subprocess runner, and public backend.gateway imports across lane-f."""
    norm_defs: list[str] = []
    journey_subproc: list[str] = []
    gateway_internals: list[str] = []
    loopbench_imports: list[str] = []

    for pkg in LANE_F_PACKAGES:
        for path in sorted((BACKEND / pkg).rglob("*.py")):
            rel = str(path.relative_to(ROOT))
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)

            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name == "norm":
                    norm_defs.append(rel)

            if pkg == "journeys" and "subprocess.run" in src:
                journey_subproc.append(rel)
            if "backend.gateway.gateway" in src:
                gateway_internals.append(rel)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if any(a.name.split(".")[0] == "loopbench" for a in node.names):
                        loopbench_imports.append(rel)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").split(".")[0] == "loopbench":
                    loopbench_imports.append(rel)

    assert norm_defs == ["backend/case/quote.py"], f"norm() must be defined once in backend/case/quote.py: {norm_defs}"
    assert journey_subproc == [], f"Journeys must delegate to backend.extract.reader.invoke_claude_cli: {journey_subproc}"
    assert gateway_internals == [], f"Lane-F must import public backend.gateway API, not backend.gateway.gateway: {gateway_internals}"
    assert loopbench_imports == [], f"Product code must never import loopbench: {loopbench_imports}"


def test_lane_f_asymmetric_authority_and_router_contracts():
    """Verify asymmetric authority in CaseLog model and canonical Follow-Up router."""
    from backend.case.model import CLINICAL_KINDS, DOCTOR_KINDS, FORBIDDEN_KINDS

    assert set(FORBIDDEN_KINDS).isdisjoint(set(CLINICAL_KINDS))
    assert set(FORBIDDEN_KINDS).isdisjoint(set(DOCTOR_KINDS))

    router_src = (BACKEND / "api" / "router.py").read_text(encoding="utf-8")
    agent_routes = re.findall(r'@router\.\w+\(\s*["\'](/api/agent/[^"\']+)["\']', router_src)
    assert set(agent_routes) == {"/api/agent/items", "/api/agent/items/{iid}/raise"}, (
        f"Agent write surface must only expose open and raise: {agent_routes}"
    )

    api_app_lines = len((BACKEND / "api" / "app.py").read_text(encoding="utf-8").splitlines())
    assert api_app_lines <= 100, f"backend/api/app.py composition root exceeded budget: {api_app_lines} > 100"
