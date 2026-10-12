#!/usr/bin/env python3
"""Download Kokoro's model files. Tries GitHub first; if that's blocked, builds the same files from Hugging Face.

Used by install.sh (`python kokoro_fetch.py <dir>`) and by server.py's background fetch.
"""
import io
import sys
import urllib.request
from pathlib import Path

GITHUB = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
HF = "https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main/"
MODEL, VOICES = "kokoro-v1.0.int8.onnx", "voices-v1.0.bin"
# the English voices (every preset uses one of these)
HF_VOICES = [f"af_{v}" for v in "alloy aoede bella heart jessica kore nicole nova river sarah sky".split()] \
    + [f"am_{v}" for v in "adam echo eric fenrir liam michael onyx puck santa".split()] \
    + [f"bf_{v}" for v in "alice emma isabella lily".split()] \
    + [f"bm_{v}" for v in "daniel fable george lewis".split()]


def _get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def _save(path, data):
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    part.rename(path)


def _hf_voices():
    import numpy as np
    voices = {}
    for i, name in enumerate(HF_VOICES, 1):
        print(f"  voice {i}/{len(HF_VOICES)}: {name}", file=sys.stderr)
        voices[name] = np.frombuffer(_get(HF + f"voices/{name}.bin"), dtype=np.float32).reshape(-1, 1, 256)
    buf = io.BytesIO()
    np.savez(buf, **voices)   # a file object, so numpy doesn't tack ".npz" onto the name
    return buf.getvalue()


def fetch(folder):
    """Fetch whatever is missing into `folder`. Returns "GitHub", "Hugging Face", or None if nothing was needed."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    source = None
    for name, from_hf in ((MODEL, lambda: _get(HF + "onnx/model_quantized.onnx", 120)), (VOICES, _hf_voices)):
        if (folder / name).exists():
            continue
        try:
            _save(folder / name, _get(GITHUB + name, 15))
            source = source or "GitHub"
        except Exception as e:  # noqa: BLE001
            print(f"  GitHub didn't work ({e}) — using Hugging Face for {name}", file=sys.stderr)
            _save(folder / name, from_hf())
            source = "Hugging Face"
    return source


if __name__ == "__main__":
    try:
        src = fetch(sys.argv[1])
    except Exception as e:  # noqa: BLE001
        print(f"Kokoro download failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Kokoro ready (from {src})" if src else "Kokoro already downloaded")
