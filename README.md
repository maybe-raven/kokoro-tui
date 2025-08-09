# kokoro-tui

A simple TUI for on-device inference with [Kokoro TTS](https://huggingface.co/hexgrad/Kokoro-82M) built with [Textual](http://textual.textualize.io).

Features:
- Generate audio __locally__ from system clipboard, text files, or Unix socket.
- Stream generated audio in-memory with real-time per-word highlighting.
- Save generated audio to file.
- View audio generation history (in-memory only for now).
- Change Kokoro generation settings.

# Demo

https://github.com/user-attachments/assets/94d2b48a-1fc5-4f11-8cf8-43e17235decf

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

> [!Warning]
> Currently it has only been tested on MacOS. I have no idea how well it'll fare on other operating systems.
