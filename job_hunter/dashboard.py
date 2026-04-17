"""Rich terminal dashboard for viewing job listings."""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich import box
from datetime import datetime

from db import get_new_jobs, get_all_jobs, get_stats

console = Console()


def _sponsorship_badge(job: dict) -> str:
    status = job.get("sponsorship_status", "unknown")
    if status == "likely":
        return "[bold green]H1B Likely[/]"
    elif status == "unlikely":
        return "[bold red]No Sponsor[/]"
    return "[yellow]Unknown[/]"


def _source_badge(source: str) -> str:
    colors = {
        "linkedin": "blue",
        "remoteok": "cyan",
        "jobright": "yellow",
        "wellfound": "magenta",
        "myvisajobs": "green",
        "simplify": "bright_cyan",
    }
    color = colors.get(source, "white")
    return f"[{color}]{source.title()}[/]"


def show_stats():
    stats = get_stats()
    console.print()
    console.print(
        Panel(
            f"[bold]Total Jobs:[/] {stats['total']}  |  "
            f"[bold green]New:[/] {stats['new']}  |  "
            f"[bold cyan]H1B Friendly:[/] {stats['h1b_friendly']}  |  "
            f"[bold yellow]Applied:[/] {stats['applied']}",
            title="[bold]Job Hunter Dashboard[/]",
            subtitle=f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            box=box.DOUBLE,
        )
    )
    console.print()


def show_jobs(jobs: list[dict] = None, title: str = "Job Listings"):
    if jobs is None:
        jobs = get_new_jobs()

    if not jobs:
        console.print("[dim]No jobs to display.[/dim]")
        return

    table = Table(
        title=title,
        box=box.ROUNDED,
        show_lines=True,
        title_style="bold cyan",
        width=140,
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("Title", style="bold", width=30)
    table.add_column("Company", width=20)
    table.add_column("Location", width=18)
    table.add_column("Source", width=10)
    table.add_column("Sponsorship", width=14)
    table.add_column("Posted", width=16)

    for i, job in enumerate(jobs, 1):
        posted = job.get("date_posted") or job.get("first_seen", "")
        if posted:
            try:
                dt = datetime.fromisoformat(posted)
                posted = dt.strftime("%m/%d %H:%M")
            except ValueError:
                pass

        table.add_row(
            str(i),
            job.get("title", "")[:35],
            job.get("company", "")[:22],
            job.get("location", "")[:20],
            _source_badge(job.get("source", "")),
            _sponsorship_badge(job),
            posted,
        )

    console.print(table)


def show_new_jobs():
    show_stats()
    jobs = get_new_jobs()
    show_jobs(jobs, title="New Job Listings (H1B Friendly)")


def show_all_jobs():
    show_stats()
    jobs = get_all_jobs()
    show_jobs(jobs, title="All Job Listings")
