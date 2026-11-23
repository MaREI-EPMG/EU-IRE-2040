#!/usr/bin/env python3
"""Validate EU-IRE-2040 specs/ and data/ consistency for epmg.netlify.app."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS_DIR = REPO_ROOT / "specs"
DATA_DIR = REPO_ROOT / "data"

REQUIRED_SPECS = [
    "chartsInfo.json",
    "chartsTitles.json",
    "contentNavs.json",
    "routes.json",
    "scenarios.json",
    "scenarioTitles.json",
    "seriesTitles.json",
]


def load_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def iter_route_charts(routes: list[dict]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for r in routes:
        base = r["path"]
        if "charts" in r:
            for c in r["charts"]:
                out.append((base, c))
        for sub in r.get("routes", []):
            sub_path = f"{base}/{sub['path']}"
            for c in sub.get("charts", []):
                out.append((sub_path, c))
    return out


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Validate JSON syntax of all spec files
    specs: dict[str, object] = {}
    for name in REQUIRED_SPECS:
        p = SPECS_DIR / name
        if not p.exists():
            errors.append(f"Missing required spec file: specs/{name}")
            continue
        try:
            specs[name] = load_json(p)
        except Exception as exc:
            errors.append(f"Invalid JSON in specs/{name}: {exc}")

    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        return 1

    charts_info: dict = specs["chartsInfo.json"]  # type: ignore[assignment]
    charts_titles: dict = specs["chartsTitles.json"]  # type: ignore[assignment]
    content_navs: list = specs["contentNavs.json"]  # type: ignore[assignment]
    routes: list = specs["routes.json"]  # type: ignore[assignment]
    scenarios_spec: list = specs["scenarios.json"]  # type: ignore[assignment]
    scenario_titles: dict = specs["scenarioTitles.json"]  # type: ignore[assignment]
    series_titles: dict = specs["seriesTitles.json"]  # type: ignore[assignment]

    # 2. Validate scenarios.json <-> data/ dirs <-> scenarioTitles.json
    spec_scenarios = [s["name"] for s in scenarios_spec]
    data_scenarios = sorted(d.name for d in DATA_DIR.iterdir() if d.is_dir())

    for s in spec_scenarios:
        if s not in data_scenarios:
            errors.append(f"Scenario '{s}' in specs/scenarios.json has no folder under data/")
        if s not in scenario_titles:
            warnings.append(f"Scenario '{s}' missing from specs/scenarioTitles.json")
    for s in data_scenarios:
        if s not in spec_scenarios:
            warnings.append(f"Data folder data/{s} not listed in specs/scenarios.json")

    # 3. Validate contentNavs <-> routes structure
    top_route_paths = {r["path"] for r in routes}
    sub_route_paths = {
        r["path"]: {sub["path"] for sub in r.get("routes", [])}
        for r in routes
        if "routes" in r
    }
    for nav in content_navs:
        nav_path = nav["path"]
        for link in nav.get("links", []):
            target = link["to"]
            if nav_path == "*":
                if target not in top_route_paths:
                    errors.append(f"Top-level nav link '{target}' missing from specs/routes.json")
            elif nav_path.endswith("/*"):
                parent = nav_path[:-2]
                if parent not in sub_route_paths:
                    errors.append(f"Sub-nav '{nav_path}' has no matching nested route in specs/routes.json")
                elif target not in sub_route_paths[parent]:
                    errors.append(f"Sub-nav link '{parent}/{target}' missing from specs/routes.json")

    # 4. Scan all data JSON files
    data_series_by_chart: dict[str, set[str]] = defaultdict(set)
    data_scens_by_chart: dict[str, set[str]] = defaultdict(set)
    extreme_scenarios: dict[str, list[tuple[str, str, int, float]]] = defaultdict(list)

    for scen in spec_scenarios:
        scen_dir = DATA_DIR / scen
        if not scen_dir.is_dir():
            continue
        for json_file in sorted(scen_dir.glob("*.json")):
            chart = json_file.stem
            try:
                obj = load_json(json_file)
            except Exception as exc:
                errors.append(f"Invalid JSON in data/{scen}/{json_file.name}: {exc}")
                continue
            if not isinstance(obj, dict) or not isinstance(obj.get("data"), list):
                errors.append(f"Malformed schema in data/{scen}/{json_file.name}")
                continue
            data_scens_by_chart[chart].add(scen)
            for ser in obj["data"]:
                sn = ser["seriesName"]
                data_series_by_chart[chart].add(sn)
                for yr, val in ser.get("seriesValues", []):
                    if not isinstance(yr, int):
                        errors.append(f"Non-integer year {yr!r} in data/{scen}/{chart}.json:{sn}")
                    if 2020 <= yr <= 2050 and (abs(val) > 1e8 or val < -1e4):
                        if len(extreme_scenarios[scen]) < 5:
                            extreme_scenarios[scen].append((chart, sn, yr, val))

    # 5. Validate every routed chart exists in chartsInfo, data/, and chartsTitles
    routed = iter_route_charts(routes)
    for route_path, chart in routed:
        if chart not in charts_info:
            errors.append(f"Routed chart '{chart}' ({route_path}) missing from specs/chartsInfo.json")
        if chart not in data_scens_by_chart:
            errors.append(f"Routed chart '{chart}' ({route_path}) has no JSON files under data/")
        else:
            missing_scens = [s for s in spec_scenarios if s not in data_scens_by_chart[chart]]
            if missing_scens:
                warnings.append(f"Routed chart '{chart}' ({route_path}) missing in scenarios: {missing_scens}")
        if chart not in charts_titles:
            errors.append(f"Routed chart '{chart}' ({route_path}) missing from specs/chartsTitles.json")

    # 6. Validate chartsInfo <-> data series, colorScale, and seriesTitles
    for chart, info in charts_info.items():
        snames = info.get("seriesNames", [])
        colors = info.get("colorScale", [])
        if len(snames) != len(colors):
            errors.append(f"chartsInfo[{chart}]: seriesNames ({len(snames)}) != colorScale ({len(colors)})")
        if len(snames) != len(set(snames)):
            errors.append(f"chartsInfo[{chart}]: duplicate entries in seriesNames")
        if len(colors) != len(set(colors)):
            warnings.append(f"chartsInfo[{chart}]: duplicate colors in colorScale")
        if not info.get("unit"):
            errors.append(f"chartsInfo[{chart}]: missing unit")
        if chart in data_series_by_chart:
            dropped = sorted(data_series_by_chart[chart] - set(snames))
            unused = sorted(set(snames) - data_series_by_chart[chart])
            if dropped:
                errors.append(f"chartsInfo[{chart}]: data series dropped (not in seriesNames): {dropped}")
            if unused:
                warnings.append(f"chartsInfo[{chart}]: unused series in seriesNames: {unused}")
        else:
            errors.append(f"chartsInfo[{chart}]: no matching data files under data/")
        for sn in snames:
            if sn not in series_titles:
                errors.append(f"Series '{sn}' (in {chart}) missing from specs/seriesTitles.json")

    for chart in sorted(data_series_by_chart.keys() - charts_info.keys()):
        errors.append(f"Data chart '{chart}' exists in data/ but is missing from specs/chartsInfo.json")

    for scen, samples in sorted(extreme_scenarios.items()):
        warnings.append(
            f"Scenario '{scen}' has extreme/anomalous values (possible infeasible/non-optimal solve), "
            f"e.g. {samples[0][0]}/{samples[0][1]} ({samples[0][2]}): {samples[0][3]:.2f}"
        )

    print(f"Checked {len(spec_scenarios)} scenarios, {len(routed)} routed chart slots, {len(charts_info)} chartsInfo entries.")
    print(f"Errors: {len(errors)} | Warnings: {len(warnings)}")
    for e in errors:
        print(f"  ERROR: {e}")
    for w in warnings:
        print(f"  WARN:  {w}")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
