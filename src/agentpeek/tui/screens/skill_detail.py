from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static

from agentpeek.models import PluginSkill


class SkillDetailModal(ModalScreen[None]):
    """Modal popped when the user presses Enter on a Skills row inside
    a plugin's detail card. Renders the skill's frontmatter description
    and SKILL.md body as Markdown.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q,escape", "dismiss_modal", "Close"),
    ]

    def __init__(self, skill: PluginSkill) -> None:
        super().__init__()
        self._skill = skill

    def compose(self) -> ComposeResult:
        s = self._skill
        with Container(id="skill-modal-card"):
            yield Static(
                Content(s.name).stylize("bold $primary"),
                id="skill-modal-title",
            )
            if s.source_plugin:
                yield Static(
                    Content.assemble(
                        ("from ", "$text-muted"),
                        (s.source_plugin, "bold $warning"),
                    ),
                    id="skill-modal-source",
                )
            if s.description:
                yield Static(s.description, id="skill-modal-desc")
            meta_lines: list[str] = []
            if s.when_to_use:
                meta_lines.append(f"**When to use:** {s.when_to_use}")
            if s.user_invocable is not None:
                meta_lines.append(
                    f"**User-invocable:** {'yes' if s.user_invocable else 'no'}"
                )
            if s.allowed_tools:
                meta_lines.append(
                    f"**Allowed tools:** {', '.join(s.allowed_tools)}"
                )
            if s.disallowed_tools:
                meta_lines.append(
                    f"**Disallowed tools:** {', '.join(s.disallowed_tools)}"
                )
            if meta_lines:
                yield Static("\n\n".join(meta_lines), id="skill-modal-meta")
            with VerticalScroll(id="skill-modal-body"):
                yield Markdown(s.body or "_(empty)_")

    def action_dismiss_modal(self) -> None:
        self.dismiss()
