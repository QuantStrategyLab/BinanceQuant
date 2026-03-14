#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_UPSTREAM_ROOT = Path(__file__).resolve().parents[1] / "CryptoLeaderRotation"
DEFAULT_REPORTS_DIR = Path("reports")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a reporting-only monthly AI briefing package from the existing shadow monitor outputs."
    )
    parser.add_argument("--upstream-root", default=str(DEFAULT_UPSTREAM_ROOT), help="Path to the CryptoLeaderRotation repo.")
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS_DIR), help="Directory containing shadow monitor outputs.")
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(str(path))
    frame = pd.read_csv(path)
    frame = frame.loc[:, ~frame.columns.str.startswith("Unnamed:")]
    if frame.empty:
        raise ValueError(f"CSV is empty: {path}")
    return frame


def _require_row(frame: pd.DataFrame, **filters: Any) -> dict[str, Any]:
    filtered = frame.copy()
    for column, value in filters.items():
        filtered = filtered.loc[filtered[column] == value]
    if filtered.empty:
        raise ValueError(f"Missing required row for filters: {filters}")
    return filtered.iloc[0].to_dict()


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        converted = float(value)
    except Exception:
        return None
    if pd.isna(converted):
        return None
    return converted


def _fmt_pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.{digits}f}%"


def _fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def load_briefing_inputs(upstream_root: Path, reports_dir: Path) -> dict[str, Any]:
    upstream_files = {
        "monthly_shadow_build_summary": upstream_root / "data/output/monthly_shadow_build_summary.json",
        "upstream_track_summary": upstream_root / "data/output/shadow_candidate_tracks/track_summary.csv",
        "live_pool": upstream_root / "data/output/live_pool.json",
        "release_manifest": upstream_root / "data/output/release_manifest.json",
    }
    downstream_files = {
        "track_summary": reports_dir / "shadow_candidate_track_summary.csv",
        "side_by_side": reports_dir / "shadow_candidate_side_by_side_summary.csv",
        "watchlist": reports_dir / "shadow_candidate_promotion_watchlist.csv",
        "sensitivity": reports_dir / "shadow_candidate_sensitivity_summary.csv",
        "concentration": reports_dir / "shadow_candidate_concentration_summary.csv",
        "regime": reports_dir / "shadow_candidate_regime_summary.csv",
    }

    missing = [str(path) for path in list(upstream_files.values()) + list(downstream_files.values()) if not path.exists()]
    if missing:
        joined = "\n- ".join(missing)
        raise FileNotFoundError(
            "Missing required monthly inputs.\n"
            "Run these first:\n"
            "- make monthly-shadow-build (in CryptoLeaderRotation)\n"
            "- make monthly-shadow-monitor (in BinanceQuant)\n"
            f"Missing files:\n- {joined}"
        )

    return {
        "upstream": {
            "monthly_shadow_build_summary": _load_json(upstream_files["monthly_shadow_build_summary"]),
            "upstream_track_summary": _load_csv(upstream_files["upstream_track_summary"]),
            "live_pool": _load_json(upstream_files["live_pool"]),
            "release_manifest": _load_json(upstream_files["release_manifest"]),
            "paths": {name: str(path) for name, path in upstream_files.items()},
        },
        "downstream": {
            "track_summary": _load_csv(downstream_files["track_summary"]),
            "side_by_side": _load_csv(downstream_files["side_by_side"]),
            "watchlist": _load_csv(downstream_files["watchlist"]),
            "sensitivity": _load_csv(downstream_files["sensitivity"]),
            "concentration": _load_csv(downstream_files["concentration"]),
            "regime": _load_csv(downstream_files["regime"]),
            "paths": {name: str(path) for name, path in downstream_files.items()},
        },
    }


def derive_risk_flags(
    challenger_track: dict[str, Any],
    watchlist: dict[str, Any],
    concentration: dict[str, Any],
) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    recent_12 = _safe_float(watchlist.get("recent_12_month_outperformance_rate")) or 0.0
    recent_6 = _safe_float(watchlist.get("recent_6_month_outperformance_rate")) or 0.0
    top5 = _safe_float(watchlist.get("top_5_positive_excess_share")) or 0.0
    risk_off = _safe_float(watchlist.get("risk_off_excess_vs_baseline"))
    lag_status = str(watchlist.get("lag_sensitivity_status", "unknown"))
    friction_status = str(watchlist.get("friction_sensitivity_status", "unknown"))
    static_pct = _safe_float(challenger_track.get("static_pct")) or 0.0
    last_known_good_pct = _safe_float(challenger_track.get("last_known_good_pct")) or 0.0

    if recent_12 < 0.50:
        flags.append(
            {
                "code": "recent_12m_breadth",
                "severity": "medium",
                "message": f"Recent 12-month outperformance is only {_fmt_pct(recent_12)}.",
            }
        )
    if recent_6 < 0.34:
        flags.append(
            {
                "code": "recent_6m_breadth",
                "severity": "high",
                "message": f"Recent 6-month outperformance is only {_fmt_pct(recent_6)}.",
            }
        )
    if top5 > 0.65:
        flags.append(
            {
                "code": "positive_excess_concentration",
                "severity": "medium",
                "message": f"Top-5 positive months still explain {_fmt_pct(top5)} of positive excess.",
            }
        )
    if lag_status != "pass" or friction_status != "pass":
        flags.append(
            {
                "code": "sensitivity_warning",
                "severity": "high",
                "message": f"Lag/friction status is {lag_status}/{friction_status}.",
            }
        )
    if risk_off is not None and risk_off < 0.0:
        flags.append(
            {
                "code": "risk_off_deterioration",
                "severity": "high",
                "message": f"Risk-off excess vs baseline is negative at {_fmt_pct(risk_off)}.",
            }
        )
    if static_pct > 0.0 or last_known_good_pct > 0.0:
        flags.append(
            {
                "code": "historical_fallback_coverage",
                "severity": "info",
                "message": (
                    "Historical replay includes fallback coverage "
                    f"(static {_fmt_pct(static_pct)}, last-known-good {_fmt_pct(last_known_good_pct)}). "
                    "This is a replay-coverage caveat, not a live switch signal."
                ),
            }
        )
    if not flags:
        flags.append(
            {
                "code": "no_material_flags",
                "severity": "info",
                "message": "No material new flags beyond the standing shadow-only observation stance.",
            }
        )
    return flags


def derive_overall_status(watchlist: dict[str, Any], risk_flags: list[dict[str, str]]) -> str:
    recommendation = str(watchlist.get("recommendation", "")).strip().lower()
    high_or_medium_flags = {flag["code"] for flag in risk_flags if flag["severity"] in {"medium", "high"}}
    if recommendation == "candidate for future controlled trial":
        return "improving_shadow_candidate"
    if recommendation == "remain shadow-only":
        return "shadow_only_warning"
    if high_or_medium_flags:
        return "caution_observation"
    return "stable_observation"


def derive_briefing_recommendation(watchlist: dict[str, Any]) -> str:
    monitor_recommendation = str(watchlist.get("recommendation", "")).strip().lower()
    if monitor_recommendation == "candidate for future controlled trial":
        return "candidate to review after more months"
    if monitor_recommendation == "remain shadow-only":
        return "shadow-only warning"
    return "continue observation"


def build_interpretation(
    side_by_side: dict[str, Any],
    watchlist: dict[str, Any],
    risk_flags: list[dict[str, str]],
) -> list[str]:
    baseline_cagr = _safe_float(side_by_side.get("baseline_cagr")) or 0.0
    challenger_cagr = _safe_float(side_by_side.get("challenger_cagr")) or 0.0
    baseline_sharpe = _safe_float(side_by_side.get("baseline_sharpe")) or 0.0
    challenger_sharpe = _safe_float(side_by_side.get("challenger_sharpe")) or 0.0
    recent_12 = _safe_float(watchlist.get("recent_12_month_outperformance_rate")) or 0.0
    recent_6 = _safe_float(watchlist.get("recent_6_month_outperformance_rate")) or 0.0
    top5 = _safe_float(watchlist.get("top_5_positive_excess_share")) or 0.0
    risk_off = _safe_float(watchlist.get("risk_off_excess_vs_baseline"))
    lag_status = str(watchlist.get("lag_sensitivity_status", "unknown"))
    friction_status = str(watchlist.get("friction_sensitivity_status", "unknown"))

    lines = []
    if challenger_cagr > baseline_cagr and challenger_sharpe > baseline_sharpe:
        lines.append("Challenger still leads cumulatively over the full downstream shadow replay window.")
    else:
        lines.append("Challenger no longer has a clear cumulative edge over the baseline.")

    if recent_12 < 0.50 or recent_6 < 0.50:
        lines.append("Recent breadth remains weak, so the challenger edge is not yet broad-based.")
    else:
        lines.append("Recent breadth is broad enough to support continued close review.")

    if top5 > 0.65:
        lines.append("The challenger advantage is still fairly concentrated in a small number of strong months.")
    else:
        lines.append("The challenger advantage is no longer dominated by just a few positive months.")

    if lag_status == "pass" and friction_status == "pass":
        lines.append("Lag and friction checks still pass.")
    else:
        lines.append("One or more lag/friction checks weakened and need extra attention.")

    if risk_off is not None and risk_off >= 0.0:
        lines.append("Risk-off behavior is not worse than baseline.")
    elif risk_off is not None:
        lines.append("Risk-off behavior is currently worse than baseline.")

    if any(flag["code"] == "historical_fallback_coverage" for flag in risk_flags):
        lines.append("Replay source coverage still includes early fallback periods, so that caveat should stay visible in reviews.")

    lines.append("Continue observation remains the right stance unless future months broaden the edge materially.")
    return lines


def build_chatgpt_questions(payload: dict[str, Any]) -> list[str]:
    return [
        "Is the challenger improving in a meaningful way, or is the current advantage still too fragile?",
        "Does the concentration risk still look too high to justify any future controlled trial planning?",
        "Do the recent 12-month and 6-month breadth metrics suggest genuine progress or continued weakness?",
        "Given the lag and friction results, does the shadow candidate still deserve continued observation?",
        "What, if anything, should be researched next upstream before reconsidering the shadow-only stance?",
    ]


def build_briefing_payload(inputs: dict[str, Any]) -> dict[str, Any]:
    upstream_summary = inputs["upstream"]["monthly_shadow_build_summary"]
    upstream_track_summary = inputs["upstream"]["upstream_track_summary"]
    live_pool = inputs["upstream"]["live_pool"]
    release_manifest = inputs["upstream"]["release_manifest"]

    track_summary = inputs["downstream"]["track_summary"]
    side_by_side = inputs["downstream"]["side_by_side"]
    watchlist = inputs["downstream"]["watchlist"]
    concentration = inputs["downstream"]["concentration"]
    sensitivity = inputs["downstream"]["sensitivity"]

    official_upstream_track = _require_row(upstream_track_summary, track_id="official_baseline")
    challenger_upstream_track = _require_row(upstream_track_summary, track_id="challenger_topk_60")
    official_downstream_track = _require_row(track_summary, track_id="official_baseline")
    challenger_downstream_track = _require_row(track_summary, track_id="challenger_topk_60")
    side_by_side_row = side_by_side.iloc[0].to_dict()
    watchlist_row = watchlist.iloc[0].to_dict()
    concentration_row = _require_row(concentration, profile="challenger_topk_60")

    risk_flags = derive_risk_flags(challenger_downstream_track, watchlist_row, concentration_row)
    overall_status = derive_overall_status(watchlist_row, risk_flags)
    briefing_recommendation = derive_briefing_recommendation(watchlist_row)
    interpretation = build_interpretation(side_by_side_row, watchlist_row, risk_flags)
    questions = build_chatgpt_questions({})

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "as_of_date": upstream_summary.get("as_of_date", live_pool.get("as_of_date", "")),
        "overall_status": overall_status,
        "system_status": {
            "baseline_role": "official_live_reference",
            "challenger_role": "shadow_only_candidate",
            "production_switch": "none",
            "source_project": live_pool.get("source_project"),
            "official_version": upstream_summary.get("official_baseline", {}).get("version", live_pool.get("version")),
            "official_mode": upstream_summary.get("official_baseline", {}).get("mode", live_pool.get("mode")),
            "official_pool_size": int(live_pool.get("pool_size", 0)),
            "official_release_count": int(official_upstream_track.get("release_count", 0)),
            "challenger_release_count": int(challenger_upstream_track.get("release_count", 0)),
            "official_last_as_of_date": official_upstream_track.get("last_as_of_date"),
            "challenger_last_as_of_date": challenger_upstream_track.get("last_as_of_date"),
            "release_manifest_version": release_manifest.get("version"),
        },
        "headline_metrics": {
            "baseline": {
                "cagr": _safe_float(side_by_side_row.get("baseline_cagr")),
                "sharpe": _safe_float(side_by_side_row.get("baseline_sharpe")),
                "max_drawdown": _safe_float(side_by_side_row.get("baseline_max_drawdown")),
                "turnover": _safe_float(side_by_side_row.get("baseline_turnover")),
            },
            "challenger": {
                "cagr": _safe_float(side_by_side_row.get("challenger_cagr")),
                "sharpe": _safe_float(side_by_side_row.get("challenger_sharpe")),
                "max_drawdown": _safe_float(side_by_side_row.get("challenger_max_drawdown")),
                "turnover": _safe_float(side_by_side_row.get("challenger_turnover")),
            },
            "delta": {
                "cagr": _safe_float(side_by_side_row.get("delta_cagr")),
                "sharpe": _safe_float(side_by_side_row.get("delta_sharpe")),
                "max_drawdown": _safe_float(side_by_side_row.get("delta_max_drawdown")),
                "turnover": _safe_float(side_by_side_row.get("delta_turnover")),
            },
        },
        "watch_metrics": {
            "recent_12_month_outperformance_rate": _safe_float(watchlist_row.get("recent_12_month_outperformance_rate")),
            "recent_6_month_outperformance_rate": _safe_float(watchlist_row.get("recent_6_month_outperformance_rate")),
            "top_5_positive_excess_share": _safe_float(watchlist_row.get("top_5_positive_excess_share")),
            "risk_off_excess_vs_baseline": _safe_float(watchlist_row.get("risk_off_excess_vs_baseline")),
            "lag_sensitivity_status": str(watchlist_row.get("lag_sensitivity_status", "")),
            "friction_sensitivity_status": str(watchlist_row.get("friction_sensitivity_status", "")),
            "monitor_recommendation": str(watchlist_row.get("recommendation", "")),
            "months_outperforming": int(concentration_row.get("months_outperforming", 0)),
            "months_compared": int(concentration_row.get("months_compared", 0)),
        },
        "risk_flags": risk_flags,
        "interpretation": interpretation,
        "recommendation": {
            "briefing_category": briefing_recommendation,
            "monitor_recommendation": str(watchlist_row.get("recommendation", "")),
            "reporting_only": True,
        },
        "sensitivity_scenarios": sensitivity.to_dict("records"),
        "operator_checklist": [
            "Run `make monthly-shadow-build` in CryptoLeaderRotation.",
            "Run `make monthly-shadow-monitor` in BinanceQuant.",
            "Run `make monthly-ai-briefing` in BinanceQuant.",
            "Review `reports/monthly_ai_review.md` and paste `reports/monthly_chatgpt_prompt.md` into ChatGPT if you want a second opinion.",
        ],
        "questions_for_chatgpt": questions,
        "source_files": {
            "upstream": inputs["upstream"]["paths"],
            "downstream": inputs["downstream"]["paths"],
        },
        "track_identity": {
            "official_baseline": {
                "profile_name": official_upstream_track.get("profile_name"),
                "source_track": official_upstream_track.get("source_track"),
                "candidate_status": official_upstream_track.get("candidate_status"),
                "release_index_path": official_upstream_track.get("release_index_path"),
            },
            "challenger_topk_60": {
                "profile_name": challenger_upstream_track.get("profile_name"),
                "source_track": challenger_upstream_track.get("source_track"),
                "candidate_status": challenger_upstream_track.get("candidate_status"),
                "release_index_path": challenger_upstream_track.get("release_index_path"),
            },
        },
    }
    return payload


def render_review_markdown(payload: dict[str, Any]) -> str:
    baseline = payload["headline_metrics"]["baseline"]
    challenger = payload["headline_metrics"]["challenger"]
    watch = payload["watch_metrics"]
    system = payload["system_status"]
    risk_lines = "\n".join(f"- {flag['severity']}: {flag['message']}" for flag in payload["risk_flags"])
    interpretation_lines = "\n".join(f"- {line}" for line in payload["interpretation"])
    checklist_lines = "\n".join(f"{idx}. {item}" for idx, item in enumerate(payload["operator_checklist"], start=1))

    return f"""# Monthly AI Review

Generated: {payload['generated_at_utc']}

## Current system status

- Baseline remains the official/live reference.
- `challenger_topk_60` remains shadow-only.
- No production switch has happened.
- Upstream as-of date: {payload['as_of_date']}
- Official version/mode: {system['official_version']} / {system['official_mode']}
- Overall status: {payload['overall_status']}

## Current headline metrics

| Metric | Baseline | Challenger |
|---|---:|---:|
| CAGR | {_fmt_pct(baseline['cagr'])} | {_fmt_pct(challenger['cagr'])} |
| Sharpe | {_fmt_num(baseline['sharpe'])} | {_fmt_num(challenger['sharpe'])} |
| Max drawdown | {_fmt_pct(baseline['max_drawdown'])} | {_fmt_pct(challenger['max_drawdown'])} |
| Turnover | {_fmt_num(baseline['turnover'])} | {_fmt_num(challenger['turnover'])} |

## Current watch metrics

- Recent 12-month outperformance rate: {_fmt_pct(watch['recent_12_month_outperformance_rate'])}
- Recent 6-month outperformance rate: {_fmt_pct(watch['recent_6_month_outperformance_rate'])}
- Top-5 positive excess share: {_fmt_pct(watch['top_5_positive_excess_share'])}
- Risk-off excess vs baseline: {_fmt_pct(watch['risk_off_excess_vs_baseline'])}
- Lag status: {watch['lag_sensitivity_status']}
- Friction status: {watch['friction_sensitivity_status']}
- Monitor recommendation: {watch['monitor_recommendation']}

## Current interpretation in simple language

{interpretation_lines}

## Risk flags

{risk_lines}

## Recommendation

- Briefing category: {payload['recommendation']['briefing_category']}
- Monitor recommendation: {payload['recommendation']['monitor_recommendation']}
- This is reporting-only. It is not a switch instruction.

## Operator checklist

{checklist_lines}
"""


def render_chatgpt_prompt(payload: dict[str, Any]) -> str:
    baseline = payload["headline_metrics"]["baseline"]
    challenger = payload["headline_metrics"]["challenger"]
    watch = payload["watch_metrics"]
    questions = "\n".join(f"{idx}. {question}" for idx, question in enumerate(payload["questions_for_chatgpt"], start=1))
    return f"""Please review this monthly shadow-monitor briefing.

Context:
- Baseline remains the official/live production reference.
- `challenger_topk_60` remains shadow-only.
- No production switch has happened.
- The current output is reporting-only and should not be treated as an automatic switch instruction.

Latest setup:
- Upstream as-of date: {payload['as_of_date']}
- Official version/mode: {payload['system_status']['official_version']} / {payload['system_status']['official_mode']}
- Overall status: {payload['overall_status']}

Headline metrics:
- Baseline: CAGR {_fmt_pct(baseline['cagr'])}, Sharpe {_fmt_num(baseline['sharpe'])}, max drawdown {_fmt_pct(baseline['max_drawdown'])}, turnover {_fmt_num(baseline['turnover'])}
- Challenger: CAGR {_fmt_pct(challenger['cagr'])}, Sharpe {_fmt_num(challenger['sharpe'])}, max drawdown {_fmt_pct(challenger['max_drawdown'])}, turnover {_fmt_num(challenger['turnover'])}

Watch metrics:
- Recent 12-month outperformance rate: {_fmt_pct(watch['recent_12_month_outperformance_rate'])}
- Recent 6-month outperformance rate: {_fmt_pct(watch['recent_6_month_outperformance_rate'])}
- Top-5 positive excess share: {_fmt_pct(watch['top_5_positive_excess_share'])}
- Risk-off excess vs baseline: {_fmt_pct(watch['risk_off_excess_vs_baseline'])}
- Lag status: {watch['lag_sensitivity_status']}
- Friction status: {watch['friction_sensitivity_status']}
- Current monitor recommendation: {watch['monitor_recommendation']}

Interpretation:
{chr(10).join(f"- {line}" for line in payload['interpretation'])}

Risk flags:
{chr(10).join(f"- {flag['severity']}: {flag['message']}" for flag in payload['risk_flags'])}

Current briefing recommendation:
- {payload['recommendation']['briefing_category']}

Questions:
{questions}
"""


def write_outputs(payload: dict[str, Any], reports_dir: Path) -> dict[str, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    review_md_path = reports_dir / "monthly_ai_review.md"
    review_json_path = reports_dir / "monthly_ai_review.json"
    prompt_path = reports_dir / "monthly_chatgpt_prompt.md"

    review_md_path.write_text(render_review_markdown(payload), encoding="utf-8")
    review_json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    prompt_path.write_text(render_chatgpt_prompt(payload), encoding="utf-8")
    return {
        "review_markdown": review_md_path,
        "review_json": review_json_path,
        "chatgpt_prompt": prompt_path,
    }


def main() -> None:
    args = parse_args()
    upstream_root = Path(args.upstream_root).resolve()
    reports_dir = Path(args.reports_dir).resolve()

    inputs = load_briefing_inputs(upstream_root, reports_dir)
    payload = build_briefing_payload(inputs)
    outputs = write_outputs(payload, reports_dir)

    print(f"as_of_date={payload['as_of_date']}")
    print(f"overall_status={payload['overall_status']}")
    print(f"briefing_recommendation={payload['recommendation']['briefing_category']}")
    print(f"review_markdown={outputs['review_markdown']}")
    print(f"review_json={outputs['review_json']}")
    print(f"chatgpt_prompt={outputs['chatgpt_prompt']}")


if __name__ == "__main__":
    main()
