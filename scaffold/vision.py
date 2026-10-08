"""Stage 3 scaffold: ask the detector (a vision model) whether one picture shows stringing.

    from scaffold.vision import ask_vision
    answer = ask_vision("out/frames/frame_026.jpg")
    # {"stringing": "light", "confidence": 0.8, "note": "thin hairs between the pillars"}

    python -m scaffold.vision picture1.jpg picture2.jpg     # try pictures from the terminal

One picture, one fixed question, one answer in a fixed form:
    stringing   "none", "light" or "heavy"; None if the model's answer could not be read
    confidence  0.0 to 1.0, the model's own estimate
    note        a few words from the model about what it saw

The key is read from OPENAI_API_KEY in .env and is never printed. The model is
OPENAI_VISION_MODEL in .env (default below). Each call costs money, so calls are
at least MIN_SECONDS apart. Standard library only: nothing to install.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config  # noqa: F401  (loads .env into the environment)

DEFAULT_MODEL = "gpt-6-luna"
MIN_SECONDS = 2.0
LEVELS = ("none", "light", "heavy")
URL = "https://api.openai.com/v1/chat/completions"

QUESTION = (
    "This is one photo, taken from the side, of a small FDM 3D print in progress: a flat green plate "
    "with two square pillars. Stringing is thin hairs or wisps of plastic left between the pillars, "
    "or trailing from them, when the nozzle travels across the gap. Grade the stringing you can see:\n"
    "none: no hairs visible;\n"
    "light: one or a few thin hairs;\n"
    "heavy: many hairs, thick strands, or a web across the gap.\n"
    'Answer only with JSON: {"stringing": "none" | "light" | "heavy", "confidence": 0.0 to 1.0, '
    '"note": "at most ten words on what you saw"}.'
)


class VisionError(RuntimeError):
    """The call failed: no key, no network, a refused key, or an unknown model."""


_last_call = 0.0


def _key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key or key in ("...", "sk-..."):
        raise VisionError("OPENAI_API_KEY is missing in .env. Your group's key goes there; never paste it into a chat.")
    return key


def ask_vision(picture: str | os.PathLike) -> dict:
    """Send one picture with the fixed question and return the answer in the fixed form."""
    global _last_call
    if os.environ.get("VISION_PROVIDER", "openai") != "openai":
        raise VisionError("Only VISION_PROVIDER=openai is supported in this course.")
    picture = Path(picture)
    if not picture.is_file():
        raise VisionError(f"No picture at {picture}")
    wait = MIN_SECONDS - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    b64 = base64.b64encode(picture.read_bytes()).decode()
    body = {
        "model": os.environ.get("OPENAI_VISION_MODEL", DEFAULT_MODEL),
        # max_completion_tokens, not max_tokens: newer models refuse max_tokens, and models that
        # reason before answering need room for that as well as for the short JSON answer.
        "max_completion_tokens": 2000,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": QUESTION},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
        ]}],
    }
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {_key()}", "Content-Type": "application/json"})
    _last_call = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            reply = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode()).get("error", {}).get("message", "")
        except Exception:
            detail = ""
        hint = {401: "the key was refused: check the OPENAI_API_KEY line in .env",
                403: "the key is not allowed to do this: it needs the Chat completions permission",
                404: "the model was not found: check OPENAI_VISION_MODEL in .env",
                429: "the spend limit or the rate limit was reached: tell the course team"}.get(exc.code, "")
        raise VisionError(f"The vision call failed ({exc.code}): {hint}. {detail[:200]}") from None
    except urllib.error.URLError as exc:
        raise VisionError(f"Could not reach the vision service: {exc.reason}") from None
    text = reply["choices"][0]["message"]["content"] or ""
    return parse_answer(text)


def parse_answer(text: str) -> dict:
    """Turn the model's text into the fixed form. A reply that does not fit gives stringing=None."""
    try:
        d = json.loads(text[text.find("{"): text.rfind("}") + 1])
        level = str(d.get("stringing", "")).strip().lower()
        if level not in LEVELS:
            raise ValueError(level)
        conf = min(1.0, max(0.0, float(d.get("confidence", 0))))
        return {"stringing": level, "confidence": round(conf, 2), "note": str(d.get("note", ""))[:80]}
    except Exception:
        return {"stringing": None, "confidence": 0.0, "note": f"unreadable answer: {text[:60]}"}


def main(argv: list[str]) -> int:
    if not argv:
        print("Give one or more picture files, for example: python -m scaffold.vision out/frame.jpg")
        return 2
    print(f"Model: {os.environ.get('OPENAI_VISION_MODEL', DEFAULT_MODEL)}")
    for name in argv:
        try:
            a = ask_vision(name)
            print(f"{Path(name).name}: stringing={a['stringing']} confidence={a['confidence']} note={a['note']}")
        except VisionError as exc:
            print(f"{Path(name).name}: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
