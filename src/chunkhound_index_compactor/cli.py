"""CLI entry point for chunkhound-index-compactor."""

from __future__ import annotations

from pathlib import Path

import click
import typer
from typer.core import TyperGroup

from .core import compact_database, human_size, replace_with_compacted, restore_indexes


class DefaultCommandGroup(TyperGroup):
    """Route a bare first argument that isn't a known command to `compact`.

    Keeps `chunkhound-index-compactor SOURCE [TARGET] [OPTIONS]` working while
    still exposing `restore` as a real subcommand.
    """

    default_command = "compact"

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        if args and args[0] not in self.commands and not args[0].startswith("-"):
            args = [self.default_command, *args]
        return super().resolve_command(ctx, args)


app = typer.Typer(add_completion=False, cls=DefaultCommandGroup)


@app.command()
def compact(
    source: Path = typer.Argument(  # noqa: B008
        ...,
        help="Path to the existing DuckDB file",
    ),
    target: Path = typer.Argument(  # noqa: B008
        default=None,
        help="Path for the compacted output [default: <source>.compacted]",
    ),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="After success, replace source with the compacted file (original moved to <source>.bak).",
    ),
    skip_hnsw: bool = typer.Option(
        False,
        "--skip-hnsw",
        help="Do not rebuild HNSW vector indexes; write a recipe table so `restore` can rebuild them later.",
    ),
) -> None:
    """Compact a DuckDB database by rebuilding it into a fresh file.

    The source is streamed table-by-table in foreign-key order into a freshly
    allocated file, which reclaims orphaned blocks and avoids the foreign-key
    race that `COPY FROM DATABASE` hits on large databases.

    With --skip-hnsw the vector indexes are left out (RAM-flat, much smaller
    output) and a recipe table records how to rebuild them; run `restore` on a
    RAM-capable machine afterwards.

    Close any process that holds a writer on the source before running. The
    source is attached read-only, but an active writer holds the file lock.
    """
    if not source.is_file():
        typer.echo(f"error: source database not found: {source}", err=True)
        raise typer.Exit(code=1)

    if target is None:
        target = source.with_suffix(source.suffix + ".compacted")

    typer.echo(f"compacting {source} ({human_size(source.stat().st_size)}) -> {target}")

    try:
        result = compact_database(source, target, skip_hnsw=skip_hnsw)
    except (FileExistsError, FileNotFoundError, ValueError, RuntimeError, OSError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from e

    typer.echo(
        f"done:      {result.target} ({human_size(result.target_size)}, {result.delta_pct:+.1f}%)"
    )

    final_path = result.target
    if replace:
        try:
            backup = replace_with_compacted(result.source, result.target)
        except (FileExistsError, FileNotFoundError, OSError) as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(code=1) from e
        typer.echo(f"replaced:  {result.source} (backup at {backup})")
        final_path = result.source

    if skip_hnsw:
        typer.echo(
            "note:      vector indexes were skipped; run "
            f"`chunkhound-index-compactor restore {final_path}` before accelerated search works."
        )
        if replace:
            typer.echo(
                f"warning:   {final_path} now has no vector index; semantic search will run "
                "brute-force until `restore` rebuilds the HNSW."
            )


@app.command()
def restore(
    database: Path = typer.Argument(  # noqa: B008
        ...,
        help="Path to a --skip-hnsw artifact to rebuild vector indexes in place",
    ),
) -> None:
    """Rebuild HNSW vector indexes in a --skip-hnsw artifact, in place.

    Reads the recipe table written by `compact --skip-hnsw` and recreates each
    index with its recorded metric. Rebuilding loads the index into RAM, so run
    this on a RAM-capable machine.
    """
    try:
        result = restore_indexes(database)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from e

    if result.restored:
        typer.echo(f"restored:  {database} ({', '.join(result.restored)})")
    else:
        typer.echo(f"no-op:     {database} (all recipe indexes already present)")
