"""`haltai` command-line interface."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import typer

from haltai import __version__
from haltai.backends import make_backend
from haltai.checks import run_static_checks
from haltai.config import BackendKind, HaltAIConfig, load_config
from haltai.drill import assess
from haltai.fingerprint import fingerprint as compute_fingerprint
from haltai.manifest import DeploymentManifest, check_prerequisites, load_manifest
from haltai.report import RESULT_LABEL, write_report
from haltai.safety import SafetyError, preflight
from haltai.scenarios import SCENARIOS
from haltai.templates import CONFIG_TEMPLATE, MANIFEST_TEMPLATE

app = typer.Typer(help="HaltAI: containability assurance for registered AI-agent deployments.",
                  no_args_is_help=True)

ConfigOpt = typer.Option(None, "--config", "-c", help="Path to haltai.yaml")


def _load(config: str | None, backend: str | None = None
          ) -> tuple[HaltAIConfig, DeploymentManifest]:
    cfg, base = load_config(config)
    if backend:
        cfg.backend = BackendKind(backend)
    mpath = cfg.manifest_path(base)
    if not mpath.exists():
        typer.secho(f"Manifest not found: {mpath}. Run `haltai init` first.", fg="red")
        raise typer.Exit(2)
    return cfg, load_manifest(mpath)


@app.command()
def version() -> None:
    """Print the HaltAI version."""
    typer.echo(__version__)


@app.command()
def init(directory: Path = typer.Argument(Path("."), help="Where to write the files"),
         force: bool = typer.Option(False, help="Overwrite existing files")) -> None:
    """Write a starter haltai.yaml and deployment.yaml (simulated backend)."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in (("haltai.yaml", CONFIG_TEMPLATE), ("deployment.yaml", MANIFEST_TEMPLATE)):
        p = directory / name
        if p.exists() and not force:
            typer.echo(f"skip   {p} (exists; use --force)")
            continue
        p.write_text(text, encoding="utf-8")
        typer.echo(f"wrote  {p}")
    typer.echo("\nNext: `haltai check`, then `haltai drill`. Edit haltai.yaml to switch backend.")


@app.command()
def validate(manifest: str | None = typer.Argument(None), config: str | None = ConfigOpt) -> None:
    """Validate a deployment manifest and report assessment limitations."""
    m = load_manifest(manifest) if manifest else _load(config)[1]
    issues = check_prerequisites(m)
    if not issues:
        typer.echo(f"OK: {m.name} meets v1 prerequisites")
        return
    typer.echo(f"{len(issues)} assessment limitation(s) for {m.name}:")
    for i in issues:
        typer.echo(f"  - {'[blocking] ' if i.blocking else ''}{i.field}: {i.reason}")
    raise typer.Exit(code=1)


@app.command()
def fingerprint(manifest: str | None = typer.Argument(None), config: str | None = ConfigOpt,
                live: bool = typer.Option(False, help="Include the backend's live snapshot")
                ) -> None:
    """Print the configuration fingerprint (manifest, optionally + live snapshot)."""
    if manifest and not live:
        typer.echo(compute_fingerprint(load_manifest(manifest)))
        return
    cfg, m = _load(config)
    snap = make_backend(cfg, m).snapshot() if live else None
    typer.echo(compute_fingerprint(m, snap))


@app.command()
def snapshot(config: str | None = ConfigOpt,
             out: Path = typer.Option(Path("snapshot.json"))) -> None:
    """Capture the live configuration of every bound resource (read-only)."""
    cfg, m = _load(config)
    snap = make_backend(cfg, m).snapshot()
    out.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
    typer.echo(f"wrote {out}  fingerprint={compute_fingerprint(m, snap)}")


@app.command()
def check(config: str | None = ConfigOpt,
          scenario: list[str] = typer.Option(None, "--scenario", "-s",
                                             help="Simulated failure scenario (repeatable)")
          ) -> None:
    """Run static checks only (no containment): identity, bypass, pre-staging, escape."""
    cfg, m = _load(config)
    b = make_backend(cfg, m, scenario or None)
    res = run_static_checks(b)
    lims = check_prerequisites(m) + res.limitations
    typer.echo(f"Backend: {b.name}")
    for e in res.evidence:
        typer.secho(f"  [{e.level.value}] {e.description}", fg="green")
    for e in res.escape_evidence:
        typer.secho(f"  [{e.level.value}] {e.description}", fg="green")
    for p in res.bypass_paths:
        typer.secho(f"  [bypass] {p}", fg="yellow")
    for p in res.escape.paths:
        typer.secho(f"  [escape] {p}", fg="red")
    for label in res.escape.unknown:
        typer.secho(f"  [not evaluated] {label}", fg="yellow")
    for lim in lims:
        typer.secho(f"  [{'blocking' if lim.blocking else 'limitation'}] {lim.field}: "
                    f"{lim.reason}", fg="red" if lim.blocking else "yellow")
    if any(lim.blocking for lim in lims) or res.bypass_paths or res.escape.paths:
        raise typer.Exit(1)


@app.command()
def drill(config: str | None = ConfigOpt,
          claim: str = typer.Option("all", help="1, 2, 3 or all"),
          scenario: list[str] = typer.Option(None, "--scenario", "-s",
                                             help="Simulated failure scenario (repeatable)"),
          out: Path | None = typer.Option(None, help="Report directory"),
          confirm: bool = typer.Option(False, "--confirm",
                                       help="Required for real (aws) drills"),
          accept_limitations: bool = typer.Option(False, "--accept-limitations"),
          quiet: bool = typer.Option(False, "--quiet", "-q")) -> None:
    """Run containment drills and write an evidence report."""
    cfg, m = _load(config)
    claims = {"1", "2", "3"} if claim == "all" else {claim}
    b = make_backend(cfg, m, scenario or None)
    try:
        # Guards that need no inspection run first, so nothing is touched without --confirm.
        preflight(b, check_prerequisites(m), confirm=confirm,
                  accept_limitations=accept_limitations)
        preflight(b, run_static_checks(b).limitations, confirm=confirm,
                  accept_limitations=accept_limitations)
    except SafetyError as exc:
        typer.secho(f"Refusing to run: {exc}", fg="red")
        raise typer.Exit(3) from exc
    report = assess(b, cfg.drill, claims, list(scenario or cfg.simulation.scenarios
                                                if b.name == "simulated" else []),
                    log=None if quiet else typer.echo)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    j, md = write_report(report, out or Path(cfg.drill.reports_dir) / f"{m.name}-{stamp}")
    typer.echo("")
    for c in report.claims:
        typer.secho(f"{c.id.value}: {RESULT_LABEL[c.result.value]}",
                    fg={"verified": "green", "failed": "red"}.get(c.result.value, "yellow"))
    typer.echo(f"Report: {md}\n        {j}")


@app.command()
def rollback(config: str | None = ConfigOpt,
             confirm: bool = typer.Option(False, "--confirm")) -> None:
    """Remove HaltAI containment from the deployment (recovery after an interrupted drill)."""
    cfg, m = _load(config)
    b = make_backend(cfg, m)
    if b.is_real and not confirm:
        typer.secho("Pass --confirm to change the real deployment.", fg="red")
        raise typer.Exit(3)
    b.rollback_containment()
    typer.echo(f"Rollback requested for {m.containment_policy.name}. "
               f"Containment active now: {b.containment_active()}")


@app.command()
def scenarios() -> None:
    """List simulated failure scenarios and the results HaltAI must report."""
    for s in SCENARIOS.values():
        typer.echo(f"{s.name:24} claim1={s.claim1.value:9} claim2={s.claim2.value:9} "
                   f"claim3={s.claim3.value:9} {s.description}")


@app.command()
def matrix(config: str | None = ConfigOpt,
           out: Path | None = typer.Option(None, help="Write a Markdown matrix here")) -> None:
    """Run every simulated scenario and compare actual with expected results."""
    cfg, m = _load(config, backend="simulated")
    rows = ["| Scenario | Claim 1 | Claim 2 | Claim 3 | Collateral | Residual | Recovery "
            "| Match |", "|---|---|---|---|---|---|---|---|"]
    all_ok = True
    for s in SCENARIOS.values():
        b = make_backend(cfg, m, [s.name])
        r = assess(b, cfg.drill, {"1", "2", "3"}, [s.name])
        c1, c2, c3 = (c.result for c in r.claims)
        coll = bool(r.collateral_impact)
        resid = any(c.residual_work_items for c in r.claims)
        rec = r.recovery_verified is not False
        ok = (c1, c2, c3, coll, resid, rec) == (s.claim1, s.claim2, s.claim3,
                                               s.collateral_impact, s.residual_exposure,
                                               s.recovery_verified)
        all_ok &= ok
        rows.append(f"| {s.name} | {c1.value} | {c2.value} | {c3.value} | "
                    f"{'yes' if coll else 'no'} | "
                    f"{'yes' if resid else 'no'} | {'yes' if rec else 'NO'} | "
                    f"{'yes' if ok else 'MISMATCH'} |")
    text = "\n".join(rows)
    typer.echo(text)
    if out:
        out.write_text(text + "\n", encoding="utf-8")
    if not all_ok:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
