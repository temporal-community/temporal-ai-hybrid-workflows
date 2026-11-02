"""How the chat looks: a loose conversation, and a rigid KYC form in the middle."""

import asyncio
from collections.abc import Awaitable
from textwrap import indent

from rich.console import Console, Group
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from app.models import KycProgress

console = Console(theme=Theme({
    "agent": "bold dark_orange", "customer": "bold green", "timer": "bold magenta",
    "kyc": "cyan", "error": "red", "note": "dim",
}))
STYLES = {"Agent": "agent", "Timer": "timer"}
LABELS = {"consent": "Consent to terms", "opening account": "Open account"}


def note(text: str) -> None:
    """Print a dim note."""
    console.print(Text(text, style="note"))


def error(text: str) -> None:
    """Print an error in red."""
    console.print(Text(text, style="error"))


def say(sender: str, text: str) -> None:
    """Print a chat message from the agent or the timer."""
    console.print()
    console.print(Text(f"{sender}: ", style=STYLES[sender]) + Text(text))
    console.print()


def ask(name: str) -> str:
    """Prompt the customer and return what they type."""
    return console.input(Text(f"{name}: ", style="customer"))


def thinking() -> Status:
    """Show a spinner while the agent works."""
    return console.status("Agent is thinking…", spinner="dots")


async def countdown(seconds: int, work: Awaitable[str]) -> str:
    """Wait for `work`, counting down the review timer meanwhile."""
    task = asyncio.ensure_future(work)
    with console.status("", spinner="clock") as status:
        while not task.done():
            status.update(Text(f"Review due in {seconds}s" if seconds else
                               "Review due now: agent is checking in…", style="timer"))
            seconds = max(seconds - 1, 0)
            await asyncio.wait([task], timeout=1)
    return task.result()


class KycPanel:
    """The fixed checklist, redrawn in place as each step completes."""

    def __init__(self) -> None:
        self.progress = KycProgress("starting", [], False)
        self.notes: dict[str, str] = {}  # extra text under a step while it runs
        self.lines = 0  # lines drawn so far, to erase before redrawing
        console.print()

    def show(self, progress: KycProgress) -> None:
        """Redraw the checklist with the latest progress."""
        self.progress = progress
        self.draw()

    def note(self, stage: str, text: str) -> None:
        """Show extra text under a step while it runs."""
        self.notes[stage] = text
        self.draw()

    def ask(self, prompt: str) -> str:
        """Prompt on the line below the panel; the next redraw clears it."""
        self.draw()
        self.lines += 1
        return console.input(Text(f"{prompt}: ", style="customer"))

    def draw(self) -> None:
        """Erase the previous panel and draw it again."""
        if self.lines:
            console.file.write(f"\r\x1b[{self.lines}A\x1b[J")  # up, then clear below
        panel = self.render()
        self.lines = len(console.render_lines(panel))
        console.print(panel)

    def render(self) -> Panel:
        """Build the checklist: done, in progress, or still to come."""
        p = self.progress
        steps = ["identity check", "proof of address", "sanctions screening",
                 "consent", "opening account"]
        if p.flagged:
            steps.insert(3, "compliance review")
        rows = []
        for i, stage in enumerate(steps):
            label = LABELS.get(stage, stage.capitalize())
            if i < len(p.steps):  # done: show the recorded result
                failed = p.stage == "declined" and i == len(p.steps) - 1
                rows.append(row(Text("✗", "red") if failed else Text("✓", "green"),
                                label, Text(p.steps[i], "note")))
            elif stage == p.stage:
                rows.append(row(Text("●", "yellow"), label, Text("in progress", "yellow")))
                if stage in self.notes:
                    rows.append(Text(indent(self.notes[stage], "    "), "note"))
            else:
                rows.append(row(Text("○", "note"), label, Text("")))
        return Panel(Group(*rows), title="KYC · fixed steps, same order, every customer",
                     title_align="left", border_style="kyc", padding=(1, 2))


def row(icon: Text, label: str, detail: Text) -> Table:
    """One checklist line; a long detail wraps within its own column."""
    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=1)
    grid.add_column(width=21)
    grid.add_column()
    grid.add_row(icon, label, detail)
    return grid
