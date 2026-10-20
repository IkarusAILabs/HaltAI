"""`haltai` command-line interface."""

from __future__ import annotations

import typer

from haltai import __version__
from haltai.fingerprint import fingerprint as compute_fingerprint
from haltai.manifest import check_prerequisites, load_manifest

app = typer.Typer(help="HaltAI: containability assurance for registered AI-agent deployments.")


@app.command()
def version() -> None:
    """Print the HaltAI version."""
    typer.echo(__version__)


@app.command()
def validate(manifest: str) -> None:
    """Validate a deployment manifest and report assessment limitations."""
    m = load_manifest(manifest)
    issues = check_prerequisites(m)
    if not issues:
        typer.echo(f"OK: {m.name} meets v1 prerequisites")
        return
    typer.echo(f"{len(issues)} assessment limitation(s) for {m.name}:")
    for i in issues:
        typer.echo(f"  - {i.field}: {i.reason}")
    raise typer.Exit(code=1)


@app.command()
def fingerprint(manifest: str) -> None:
    """Print the configuration fingerprint for a manifest."""
    typer.echo(compute_fingerprint(load_manifest(manifest)))


if __name__ == "__main__":
    app()
