r"""Upgrade each v1 aquarium scene to a 3D WebGL scene using Claude Fable 5.1.

Each request gets the upgrade brief, the scene's original prompt, the v1 source
code and a v1 screenshot, so the composition carries over.

Usage:  .venv/Scripts/python aquariums-v2/generate.py            (all scenes)
        .venv/Scripts/python aquariums-v2/generate.py 1-nature   (just one)
        .venv/Scripts/python aquariums-v2/generate.py --effort medium 5-high-tech
"""

import base64
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent
V1 = ROOT.parent / "aquariums"
load_dotenv(ROOT.parent / ".env")
if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("ANTHROPIC_API_KEY is empty: paste your key into the .env file")

MODEL = "claude-fable-5-1"
EFFORT = "high"
PRICE_IN, PRICE_OUT = 10 / 1e6, 50 / 1e6  # USD per token
PROGRESS_EVERY = 30  # seconds

SYSTEM = """You upgrade existing animated aquarium scenes into real-time 3D WebGL scenes.

You receive the scene's original art brief, its current 2D canvas implementation and a
screenshot of it, plus an upgrade brief from the owner. The current implementation is the
design source of truth: keep its plants, fish and shrimp species and counts, hardscape,
substrate layout, equipment and composition, re-built in 3D. The upgrade brief asks for
photorealism; the medium is live browser rendering, so push realism as far as real-time
WebGL on an ordinary laptop allows.

Technical contract:
- Output one complete HTML document and nothing else: no markdown fences, no commentary.
- Use three.js r186 as an ES module through exactly this import map:
  {"imports": {"three": "../vendor/three/build/three.module.js",
               "three/addons/": "../vendor/three/examples/jsm/"}}
  No other external requests (no CDNs, fonts or image files); generate every texture
  procedurally (canvas, DataTexture or shaders).
- The page is shown full-bleed inside an iframe of arbitrary size. Handle resize; cap the
  renderer pixel ratio at 2.
- It must run smoothly on an integrated laptop GPU: instance repeated geometry (leaves,
  gravel, bubbles), keep draw calls and shadow-map sizes modest, and show the first frame
  within a few seconds of load.
- Animate continuously with time-based motion. Nothing readable on screen: no text, UI,
  labels, logos or watermarks.
- Your output budget is limited: plan briefly, then write compact code (aim for under
  ~55 KB) so the document is always complete, ending with </html>.
- Must run without console errors or NaN geometry in current Chrome. Before finishing,
  re-read your code for runtime-only mistakes such as an object property and a method
  sharing a name, or a unary minus directly before ** (a syntax error in JavaScript)."""

client = anthropic.Anthropic()
print_lock = threading.Lock()


def say(msg: str) -> None:
    with print_lock:
        print(msg, flush=True)


def extract_html(text: str) -> str:
    match = re.search(r"<!DOCTYPE html>.*</html>", text, re.S | re.I)
    if not match:
        raise ValueError("no complete HTML document in response")
    return match.group(0)


def build_content(name: str) -> list:
    brief = (ROOT / "prompts" / "upgrade-brief.txt").read_text(encoding="utf-8")
    original = (V1 / "prompts" / f"{name}.txt").read_text(encoding="utf-8")
    v1_code = (V1 / "scenes" / f"{name}.html").read_text(encoding="utf-8")
    shot = base64.standard_b64encode((ROOT / "reference" / f"{name}.png").read_bytes()).decode()
    return [
        {"type": "text", "text": f"<original_art_brief>\n{original}\n</original_art_brief>"},
        {"type": "text", "text": f"<current_implementation>\n{v1_code}\n</current_implementation>"},
        {"type": "text", "text": "Screenshot of the current implementation:"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": shot}},
        {"type": "text", "text": f"<upgrade_brief>\n{brief}\n</upgrade_brief>"},
    ]


def generate(name: str) -> float:
    start = time.time()
    say(f"[{name}] started")
    phase, written, next_report = "waiting", [], start + PROGRESS_EVERY
    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=128000,
            system=SYSTEM,
            output_config={"effort": EFFORT},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": build_content(name)}],
        ) as stream:
            for event in stream:
                if event.type == "content_block_start":
                    phase = {"thinking": "thinking", "text": "writing"}.get(event.content_block.type, phase)
                elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                    written.append(event.delta.text)
                now = time.time()
                if now >= next_report:
                    kb = sum(map(len, written)) // 1024
                    done = f", {kb} KB written" if written else ""
                    say(f"[{name}] {int(now - start) // 60}m{int(now - start) % 60:02d}s {phase}{done}")
                    next_report = now + PROGRESS_EVERY
            response = stream.get_final_message()
    except Exception:
        # A dropped connection mid-stream raises a transport error, not an APIError.
        # Keep whatever code arrived so it isn't lost.
        if written:
            (ROOT / "scenes" / f"{name}.raw.txt").write_text("".join(written), encoding="utf-8")
        raise

    u = response.usage
    cost = u.input_tokens * PRICE_IN + u.output_tokens * PRICE_OUT
    summary = f"{u.input_tokens} in / {u.output_tokens} out, ~${cost:.2f}, {time.time() - start:.0f}s"
    if response.model != MODEL:
        summary += f", served by {response.model}"
    if response.stop_reason == "refusal":
        say(f"[{name}] REFUSED ({summary})")
        return cost

    text = "".join(b.text for b in response.content if b.type == "text")
    if response.stop_reason == "max_tokens":
        (ROOT / "scenes" / f"{name}.raw.txt").write_text(text, encoding="utf-8")
        say(f"[{name}] CUT OFF at max_tokens; partial output saved ({summary})")
        return cost
    try:
        html = extract_html(text)
    except ValueError as e:
        (ROOT / "scenes" / f"{name}.raw.txt").write_text(text, encoding="utf-8")
        say(f"[{name}] FAILED: {e}; raw output saved ({summary})")
        return cost

    out = ROOT / "scenes" / f"{name}.html"
    if out.exists():  # keep earlier iterations for comparison
        history = ROOT / "scenes" / "history"
        history.mkdir(exist_ok=True)
        shutil.move(out, history / f"{name}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(out.stat().st_mtime))}.html")
    out.write_text(html, encoding="utf-8")
    say(f"[{name}] done: {len(html) // 1024} KB ({summary})")
    return cost


def run(name: str) -> float:
    try:
        return generate(name)
    except Exception as e:  # one scene failing must not take the others down
        say(f"[{name}] FAILED with {type(e).__name__}: {e} (cost unknown; any code received is in {name}.raw.txt)")
        return 0.0


def main() -> None:
    global EFFORT
    args = sys.argv[1:]
    if "--effort" in args:
        i = args.index("--effort")
        EFFORT = args[i + 1]
        del args[i : i + 2]
    names = [p.stem for p in sorted((V1 / "prompts").glob("*.txt"))]
    wanted = set(args)
    names = [n for n in names if not wanted or n in wanted]
    (ROOT / "scenes").mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        total = sum(pool.map(run, names))
    say(f"\nTotal estimated cost: ${total:.2f}")


if __name__ == "__main__":
    main()
