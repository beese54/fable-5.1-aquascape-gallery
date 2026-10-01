r"""Turn each aquarium prompt into an animated HTML scene using Claude Fable 5.1.

Usage:  .venv/Scripts/python aquariums/generate.py            (all prompts)
        .venv/Scripts/python aquariums/generate.py 2-iwagumi  (just one)
"""

import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent
load_dotenv(ROOT.parent / ".env")
if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("ANTHROPIC_API_KEY is empty: paste your key into the .env file")

MODEL = "claude-fable-5-1"
EFFORT = "high"
PRICE_IN, PRICE_OUT = 10 / 1e6, 50 / 1e6  # USD per token

SYSTEM = """You turn aquarium art-direction briefs into a single animated web scene.

The brief was written for a photorealistic video generator. Your medium is code running
live in a browser, so interpret it as faithfully and beautifully as that medium allows:
aim for a rich, cinematic, naturalistic look rather than a flat cartoon.

Technical contract:
- Output one complete, self-contained HTML document and nothing else: no markdown fences,
  no commentary. Inline all CSS and JS. No external requests of any kind (no CDNs, fonts,
  images); generate every texture procedurally.
- The page is shown full-bleed inside an iframe of arbitrary size. Fill the viewport with a
  dark neutral backdrop and draw the tank centred at a 2:1 width:height ratio (60 x 30 cm),
  as large as fits with a small margin. Handle resize and devicePixelRatio.
- Animate continuously with requestAnimationFrame at a smooth frame rate on an ordinary
  laptop; pre-render static layers to offscreen canvases where that helps. Use time-based
  motion so speed is independent of frame rate.
- Nothing readable on screen: no text, labels, UI, logos or watermarks.
- Must run without console errors in current Chrome."""

client = anthropic.Anthropic()


def extract_html(text: str) -> str:
    match = re.search(r"<!DOCTYPE html>.*</html>", text, re.S | re.I)
    if not match:
        raise ValueError("no complete HTML document in response")
    return match.group(0)


def generate(prompt_file: Path) -> float:
    name = prompt_file.stem
    start = time.time()
    print(f"[{name}] started")
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=128000,
        system=SYSTEM,
        output_config={"effort": EFFORT},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": prompt_file.read_text(encoding="utf-8")}],
    ) as stream:
        response = stream.get_final_message()

    u = response.usage
    cost = u.input_tokens * PRICE_IN + u.output_tokens * PRICE_OUT
    summary = f"{u.input_tokens} in / {u.output_tokens} out, ~${cost:.2f}, {time.time() - start:.0f}s"
    if response.model != MODEL:
        summary += f", served by {response.model}"

    if response.stop_reason == "refusal":
        print(f"[{name}] REFUSED ({summary})")
        return cost
    if response.stop_reason == "max_tokens":
        print(f"[{name}] CUT OFF at max_tokens ({summary})")
        return cost

    text = "".join(b.text for b in response.content if b.type == "text")
    try:
        html = extract_html(text)
    except ValueError as e:
        (ROOT / "scenes" / f"{name}.raw.txt").write_text(text, encoding="utf-8")
        print(f"[{name}] FAILED: {e}; raw output saved ({summary})")
        return cost
    (ROOT / "scenes" / f"{name}.html").write_text(html, encoding="utf-8")
    print(f"[{name}] done: {len(html) // 1024} KB ({summary})")
    return cost


def run(prompt_file: Path) -> float:
    try:
        return generate(prompt_file)
    except anthropic.APIError as e:
        print(f"[{prompt_file.stem}] API error: {e}")
        return 0.0


def main() -> None:
    wanted = set(sys.argv[1:])
    prompts = [p for p in sorted((ROOT / "prompts").glob("*.txt")) if not wanted or p.stem in wanted]
    (ROOT / "scenes").mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=len(prompts)) as pool:
        total = sum(pool.map(run, prompts))
    print(f"\nTotal estimated cost: ${total:.2f}")


if __name__ == "__main__":
    main()
