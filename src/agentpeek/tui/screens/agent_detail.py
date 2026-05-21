from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static

from agentpeek.models import PluginAgent


class AgentDetailModal(ModalScreen[None]):
    """Modal popped when the user presses Enter on an Agents row inside
    a plugin's detail card. Renders the agent's frontmatter description
    and agent markdown body.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q,question_mark,escape", "dismiss_modal", "Close"),
    ]

    def __init__(self, agent: PluginAgent) -> None:
        super().__init__()
        self._agent = agent

    def compose(self) -> ComposeResult:
        a = self._agent
        with Container(id="agent-modal-card"):
            yield Static(
                Content(a.name).stylize("bold $primary"),
                id="agent-modal-title",
            )
            if a.source_plugin:
                yield Static(
                    Content.assemble(
                        ("from ", "$text-muted"),
                        (a.source_plugin, "bold $warning"),
                    ),
                    id="agent-modal-source",
                )
            if a.description:
                yield Static(a.description, id="agent-modal-desc")
            meta_lines: list[str] = []
            if a.when_to_use:
                meta_lines.append(f"**When to use:** {a.when_to_use}")
            if a.user_invocable is not None:
                meta_lines.append(
                    f"**User-invocable:** {'yes' if a.user_invocable else 'no'}"
                )
            if a.allowed_tools:
                meta_lines.append(
                    f"**Allowed tools:** {', '.join(a.allowed_tools)}"
                )
            if a.disallowed_tools:
                meta_lines.append(
                    f"**Disallowed tools:** {', '.join(a.disallowed_tools)}"
                )
            if meta_lines:
                yield Static("\n\n".join(meta_lines), id="agent-modal-meta")
            with VerticalScroll(id="agent-modal-body"):
                yield Markdown(a.body or "_(empty)_")

    def action_dismiss_modal(self) -> None:
        self.dismiss()
