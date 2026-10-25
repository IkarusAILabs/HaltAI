"""Evidence report: ``report.json`` (machine) and ``report.md`` (human)."""

from __future__ import annotations

from pathlib import Path

from haltai.models import AssessmentReport, Claim, ClaimId

RESULT_LABEL = {
    "verified": "VERIFIED (within the tested scope)",
    "failed": "FAILED",
    "partial": "PARTIAL",
    "unknown": "UNKNOWN",
}


def _interval_line(c: Claim) -> str:
    if c.id == ClaimId.AGENT_CANNOT_UNDO:
        return "n/a (policy simulation, no containment activated)"
    st = c.interval_stats
    if not st:
        return "not measured"
    res = f", {c.poll_interval_s:g}s polling" if c.poll_interval_s else ""
    if st["n"] == 1:
        return f"denied within <= {st['max']:.0f}s (1 measurement{res})"
    return (f"denied within <= {st['max']:.0f}s worst case; median {st['median']:.0f}s, "
            f"best {st['min']:.0f}s ({st['n']} measurements{res})")


def render_markdown(r: AssessmentReport) -> str:
    lines = [
        f"# HaltAI assessment: {r.deployment_name}",
        "",
        f"- Generated: {r.generated_at.isoformat()}",
        f"- Backend: `{r.backend}`" + (f" (scenarios: {', '.join(r.scenarios)})"
                                       if r.scenarios else ""),
        f"- Fingerprint: `{r.deployment_fingerprint}`",
        f"- HaltAI version: {r.haltai_version}",
        "",
    ]
    if r.backend == "simulated":
        lines += ["> This report comes from the simulated backend. It demonstrates the method "
                  "and must not be read as evidence about a real deployment.", ""]
    lines += ["## Summary", "", "| Claim | Result | Highest evidence level | Time to denial |",
              "|---|---|---|---|"]
    for c in r.claims:
        lvl = c.highest_evidence_level.value if c.highest_evidence_level else "none"
        lines.append(f"| {c.id.value} | {RESULT_LABEL[c.result.value]} | {lvl} | "
                     f"{_interval_line(c)} |")
    rec = {True: "verified", False: "NOT verified", None: "not tested"}[r.recovery_verified]
    lines += ["", f"- Recovery after rollback: **{rec}**",
              f"- Collateral impact: {'; '.join(r.collateral_impact) or 'none observed'}", ""]

    for c in r.claims:
        lines += [f"## {c.id.value}", "", f"**Statement.** {c.statement}", "",
                  f"**Result.** {RESULT_LABEL[c.result.value]}", ""]
        lines += ["**Why.**", ""] + [f"- {w}" for w in c.rationale] + [""]
        if c.id != ClaimId.AGENT_CANNOT_UNDO:
            lines += [f"**Measured interval.** {_interval_line(c)}. This is an observation "
                      "from this test, not a guarantee.", ""]
        lines += ["**Evidence.**", "", "| Level | Type | Description | Source |",
                  "|---|---|---|---|"]
        for e in c.evidence:
            lines.append(f"| {e.level.value} | {e.type.value} | {e.description} | "
                         f"`{e.source}` |")
        lines += ["", "**Declared paths.**", ""] + [f"- {p}" for p in c.declared_paths]
        lines += ["", "**Assumptions.**", ""] + ([f"- {a}" for a in c.assumptions] or ["- none"])
        lines += ["", "**Residual exposure (not covered by this claim).**", ""]
        lines += [f"- {x}" for x in c.residual_exposure] or ["- none identified"]
        lines += ["", "**Revalidate after.**", ""] + [f"- {t}" for t in c.revalidation_triggers]
        lines.append("")

    lines += ["## Limitations", ""]
    lines += [f"- {'**blocking** ' if lim.blocking else ''}`{lim.field}`: {lim.reason}"
              for lim in r.limitations] or ["- none"]
    lines += ["", "## Timeline", "", "| Time (UTC) | Event | Detail |", "|---|---|---|"]
    for ev in r.timeline:
        lines.append(f"| {ev.at.strftime('%H:%M:%S')} | {ev.kind} | {ev.detail} |")
    lines.append("")
    return "\n".join(lines)


def write_report(r: AssessmentReport, out_dir: str | Path) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    j = out / "report.json"
    md = out / "report.md"
    j.write_text(r.model_dump_json(indent=2), encoding="utf-8")
    md.write_text(render_markdown(r), encoding="utf-8")
    return j, md
