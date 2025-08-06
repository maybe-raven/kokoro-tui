from typing import ClassVar, cast

from textual import log, on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Grid, HorizontalGroup
from textual.screen import ModalScreen
from textual.validation import ValidationResult, Validator
from textual.widgets import (
    Button,
    Input,
    Label,
    Select,
    Switch,
)
from textual.widgets._select import NoSelection

from lib.agents.kokoro import KokoroAgent


class FilepathInput(ModalScreen[str]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", key_display="esc"),
    ]

    def compose(self) -> ComposeResult:
        title_input = Input(placeholder="Enter a file path.")
        title_input.border_subtitle = (
            r"\[[white]enter[/]] Save  \[[white]esc[/]] Cancel"
        )
        yield title_input

    @on(Input.Submitted)
    def close_screen(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self):
        self.dismiss()


class ConfirmationScreen(ModalScreen[bool]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(
            "y",
            "confirm(True)",
            "Yes",
            show=False,
        ),
        Binding(
            "n",
            "confirm(False)",
            "No",
            show=False,
        ),
    ]

    def __init__(
        self,
        filename: str,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.filename = filename

    def compose(self) -> ComposeResult:
        yield Grid(
            Label(
                f"'{self.filename}' already exists. Are you sure you want to overwrite it?",
                id="question",
            ),
            Button("[Y]es", variant="error", id="overwrite"),
            Button("[N]o", variant="primary", id="cancel"),
            id="dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "overwrite":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_confirm(self, v: bool):
        self.dismiss(v)


class ConfigScreen(ModalScreen[KokoroAgent.Config]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(
            "ctrl+enter",
            "confirm",
            "Confirm",
            show=True,
        ),
        Binding("escape", "cancel", "Cancel", show=True, key_display="esc"),
    ]

    def __init__(
        self,
        config: KokoroAgent.Config,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.config = config

    def compose(self) -> ComposeResult:
        with Grid(id="config-grid"):
            yield Label("voice")
            yield Select(
                [
                    ("af_heart", "af_heart"),
                    ("af_alloy", "af_alloy"),
                    ("af_aoede", "af_aoede"),
                    ("af_bella", "af_bella"),
                    ("af_jessica", "af_jessica"),
                    ("af_kore", "af_kore"),
                    ("af_nicole", "af_nicole"),
                    ("af_nova", "af_nova"),
                    ("af_river", "af_river"),
                    ("af_sarah", "af_sarah"),
                    ("af_sky", "af_sky"),
                    ("am_adam", "am_adam"),
                    ("am_echo", "am_echo"),
                    ("am_eric", "am_eric"),
                    ("am_fenrir", "am_fenrir"),
                    ("am_liam", "am_liam"),
                    ("am_michael", "am_michael"),
                    ("am_onyx", "am_onyx"),
                    ("am_puck", "am_puck"),
                    ("am_santa", "am_santa"),
                    ("bf_alice", "bf_alice"),
                    ("bf_emma", "bf_emma"),
                    ("bf_isabella", "bf_isabella"),
                    ("bf_lily", "bf_lily"),
                    ("bm_daniel", "bm_daniel"),
                    ("bm_fable", "bm_fable"),
                    ("bm_george", "bm_george"),
                    ("bm_lewis", "bm_lewis"),
                    ("jf_alpha", "jf_alpha"),
                    ("jf_gongitsune", "jf_gongitsune"),
                    ("jf_nezumi", "jf_nezumi"),
                    ("jf_tebukuro", "jf_tebukuro"),
                    ("jm_kumo", "jm_kumo"),
                    ("zf_xiaobei", "zf_xiaobei"),
                    ("zf_xiaoni", "zf_xiaoni"),
                    ("zf_xiaoxiao", "zf_xiaoxiao"),
                    ("zf_xiaoyi", "zf_xiaoyi"),
                    ("zm_yunjian", "zm_yunjian"),
                    ("zm_yunxi", "zm_yunxi"),
                    ("zm_yunxia", "zm_yunxia"),
                    ("zm_yunyang", "zm_yunyang"),
                    ("ef_dora", "ef_dora"),
                    ("em_alex", "em_alex"),
                    ("em_santa", "em_santa"),
                    ("ff_siwis", "ff_siwis"),
                    ("hf_alpha", "hf_alpha"),
                    ("hf_beta", "hf_beta"),
                    ("hm_omega", "hm_omega"),
                    ("hm_psi", "hm_psi"),
                    ("if_sara", "if_sara"),
                    ("im_nicola", "im_nicola"),
                    ("pf_dora", "pf_dora"),
                    ("pm_alex", "pm_alex"),
                    ("pm_santa", "pm_santa"),
                ],
                value=self.config.voice,
                id="input-voice",
            )
            yield Label("speed")
            yield Input(
                value=str(self.config.speed),
                type="number",
                restrict=r"^\d*\.?\d*",
                max_length=8,
                validators=PositiveNumberValidator(),
                id="input-speed",
            )
            yield Label("split pattern")
            yield Input(
                value=self.config.split_pattern.replace("\n", "\\n"),
                id="input-pattern",
            )
            yield Label("device")
            yield Select(
                [("cpu", "cpu"), ("cuda", "cuda"), ("mps", "mps")],
                value=Select.BLANK
                if self.config.device is None
                else self.config.device,
                id="input-device",
            )
            yield Label("trf")
            yield Switch(value=self.config.trf, id="input-trf")
            yield HorizontalGroup(
                Button("Confirm", variant="primary", id="confirm"),
                Button("Cancel", variant="default", id="cancel"),
                id="button-group",
            )

    def action_confirm(self):
        self.config.voice = cast(Input, self.query_one("#input-voice")).value
        self.config.speed = float(cast(Input, self.query_one("#input-speed")).value)
        pattern = cast(Input, self.query_one("#input-pattern")).value
        self.config.split_pattern = pattern.replace("\\n", "\n")
        log(escaped_pattern=pattern, pattern=self.config.split_pattern)
        selection = cast(Select[str], self.query_one("#input-device")).value
        self.config.device = None if isinstance(selection, NoSelection) else selection
        self.config.trf = cast(Switch, self.query_one("#input-trf")).value
        try:
            self.config.save()
        except (PermissionError, IOError) as e:
            self.notify(f"Error: failed to write config file: {e}", severity="error")
        self.dismiss(self.config)

    def action_cancel(self):
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self.action_confirm()
        else:
            self.dismiss()


class PositiveNumberValidator(Validator):
    def validate(self, value: str) -> ValidationResult:
        try:
            x = float(value)
            if x > 0:
                return self.success()
            else:
                return self.failure("speed must be positive")
        except ValueError:
            return self.failure("not a number")
