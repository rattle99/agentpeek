from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Static

from agentpeek.actions.runner import ActionResult


class ActionResultModal(ModalScreen[None]):
    """Surface the raw stdout/stderr of a `claude plugin ...` call.

    Used for failures (and for successes when output is non-trivial).
    Successful one-line outcomes use `self.notify(...)` instead.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q,escape,enter", "dismiss_modal", "Close"),
    ]

    def __init__(self, result: ActionResult) -> None:
        super().__init__()
        self._result = result

    def compose(self) -> ComposeResult:
        r = self._result
        header_color = "$success" if r.ok else "$error"
        verdict = "OK" if r.ok else f"FAILED (exit {r.returncode})"
        scope_part = f" --scope {r.scope}" if r.scope else ""
        with Container(id="action-result-card"):
            yield Static(
                Content.assemble(
                    (f"{verdict}  ", f"bold {header_color}"),
                    (f"{r.verb} {r.target}{scope_part}", "$text-muted"),
                ),
                id="action-result-title",
            )
            yield Static(
                Content.assemble(
                    ("elapsed ", "$text-muted"),
                    (f"{r.elapsed_ms} ms", "bold"),
                ),
                id="action-result-meta",
            )
            with VerticalScroll(id="action-result-body"):
                if r.stdout.strip():
                    yield Static("stdout", classes="action-result-section-title")
                    yield Static(r.stdout.rstrip(), classes="action-result-stream")
                if r.stderr.strip():
                    yield Static("stderr", classes="action-result-section-title")
                    yield Static(r.stderr.rstrip(), classes="action-result-stream")
                if not r.stdout.strip() and not r.stderr.strip():
                    yield Static(
                        "(no output)",
                        classes="action-result-stream",
                    )

    def action_dismiss_modal(self) -> None:
        self.dismiss()
