"""Command line interface - prove the pipeline works here before wiring the UI.

uv run wildfire                      # interactive
uv run wildfire -q "Which areas burned in the Eaton Fire around Altadena?"
uv run wildfire --expertise expert -q "..."
uv run wildfire --graph              # export the Mermaid architecture diagram
uv run wildfire --check              # config self-check, no LLM call
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from langgraph.types import Command
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.table import Table

from .contract import AnalysisContract, SpatialSlot
from .graph import build_graph
from .graph.state import STAGE_LABELS
from .llm import check_llm_ready, describe_llm

console = Console()

_SOURCE_STYLE = {"user_stated": "green", "agent_inferred": "yellow", "default": "dim"}


def _render_contract(contract: AnalysisContract) -> None:
    header = Table.grid(padding=(0, 2))
    header.add_column(style="bold cyan")
    header.add_column()
    header.add_row("Restatement", contract.restatement or "-")
    header.add_row("Task intent", ", ".join(contract.task_intent) or "-")
    header.add_row("User role", contract.user_role)
    header.add_row("Expertise", contract.expertise)
    header.add_row("Hazard objects", ", ".join(contract.hazard_objects) or "-")
    console.print(Panel(header, title="Analysis Contract", border_style="cyan"))

    slots = Table(show_header=True, header_style="bold")
    slots.add_column("slot")
    slots.add_column("value")
    slots.add_column("source")
    slots.add_column("conf", justify="right")
    slots.add_column("blocking")
    for name, slot in contract.slots.items():
        style = _SOURCE_STYLE.get(slot.source, "")
        slots.add_row(
            name,
            slot.value or "[dim]-[/dim]",
            f"[{style}]{slot.source}[/{style}]",
            f"{slot.confidence:.2f}",
            "*" if slot.is_blocking else "",
        )
    console.print(slots)

    spatial = contract.spatial()
    if isinstance(spatial, SpatialSlot) and spatial.resolved:
        r = spatial.resolved
        console.print(
            f"[cyan]Grounded[/cyan] {r.display_name}  center={r.center}  "
            f"buffer={r.buffer_km} km  geocoder={r.geocoder}"
        )

    if contract.assumptions:
        console.print("\n[bold yellow]Assumptions - decisions made on your behalf[/bold yellow]")
        for item in contract.assumptions:
            console.print(f"  - {item}")

    if contract.unresolved:
        console.print("\n[bold red]Unresolved[/bold red]")
        for item in contract.unresolved:
            console.print(f"  - {item}")

    verdict = (
        "[bold green]ready_for_planning[/bold green]"
        if contract.ready_for_planning
        else f"[bold red]still missing: {', '.join(contract.pending_slots)}[/bold red]"
    )
    console.print(f"\n{verdict}   slots filled {contract.filled_ratio():.0%}")


def _render_interrupt(payload: dict) -> None:
    lines = [payload.get("preamble", "")]
    for idx, q in enumerate(payload.get("questions", []), 1):
        lines.append(f"\n[bold]{idx}. {q['question']}[/bold]  [dim]({q['slot']})[/dim]")
        for opt in q.get("options", []):
            mark = " [green](recommended)[/green]" if opt.get("recommended") else ""
            imp = f"\n      [dim]{opt['implication']}[/dim]" if opt.get("implication") else ""
            lines.append(f"   > {opt['label']}{mark}{imp}")
    console.print(Panel("\n".join(lines), title="Clarification needed", border_style="yellow"))


async def _run(question: str, expertise: str | None, as_json: bool) -> int:
    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    payload: dict | Command = {
        "original_request": question,
        "expertise_override": expertise,
        "clarification_rounds": 0,
        "stage": "requirement_understanding",
    }

    while True:
        state = await graph.ainvoke(payload, config=config)
        interrupts = state.get("__interrupt__")
        if not interrupts:
            break

        _render_interrupt(interrupts[0].value)
        try:
            answer = console.input("\n[bold cyan]your answer > [/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]cancelled[/dim]")
            return 130
        payload = Command(resume=answer or "you decide")

    contract: AnalysisContract = state["contract"]
    if as_json:
        console.print(JSON(contract.model_dump_json(indent=2)))
    else:
        console.print()
        _render_contract(contract)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="wildfire", description="User Goal Agent (Task 1)")
    parser.add_argument("-q", "--question", help="the user's question; omit for interactive input")
    parser.add_argument(
        "--expertise",
        choices=["general", "practitioner", "expert"],
        help="force an expertise level, overriding the agent's inference",
    )
    parser.add_argument("--json", action="store_true", help="print the raw contract JSON")
    parser.add_argument("--graph", action="store_true", help="print the Mermaid diagram and exit")
    parser.add_argument("--check", action="store_true", help="config self-check, no LLM call")
    args = parser.parse_args()

    if args.graph:
        from .graph import export_mermaid

        print(export_mermaid())
        return 0

    ok, detail = check_llm_ready()
    if args.check:
        console.print(f"{'[green]OK[/green]' if ok else '[red]FAIL[/red]'} LLM: {detail}")
        return 0 if ok else 1
    if not ok:
        console.print(f"[red]LLM not ready[/red]\n{detail}")
        return 1

    question = args.question or console.input("[bold cyan]your question > [/bold cyan]").strip()
    if not question:
        console.print("[red]The question cannot be empty[/red]")
        return 1

    console.print(f"[dim]LLM: {describe_llm()}[/dim]")
    console.print(f"[dim]Pipeline: {' -> '.join(STAGE_LABELS.values())}[/dim]\n")

    try:
        return asyncio.run(_run(question, args.expertise, args.json))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
