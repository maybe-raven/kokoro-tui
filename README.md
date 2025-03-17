# kokoro-tui

A simple TUI for on-device inference with [Kokoro TTS](https://huggingface.co/hexgrad/Kokoro-82M) built with [Textual](http://textual.textualize.io).

Features:
- Generate audio locally from system clipboard or text files.
- Play generated audio in-memory.
- Save generated audio to file.
- View audio generation history (in-memory only for now).
- Change Kokoro generation settings.

# To Run

## Using [UV](https://docs.astral.sh/uv/)

```
git clone --depth=1 https://github.com/maybe-raven/kokoro-tui
cd kokoro-tui
uv run src/main.py
```

## Using `pip`

```
git clone --depth=1 https://github.com/maybe-raven/kokoro-tui
cd kokoro-tui
python3.12 -m venv .venv
source .venv/bin/activate
pip install .
python src/main.py
```

Some of the packages used need to initialize themselves by downloading additional dependencies (e.g., Kokoro model weights files), so the first run might take a while to actually start up.
