r"""Streaming chat with Claude Fable 5.1.

Usage:  .venv\Scripts\python demo.py            (interactive chat)
        .venv\Scripts\python demo.py "prompt"   (one-shot)
"""

import os
import sys

import anthropic
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252

load_dotenv()  # reads ANTHROPIC_API_KEY from .env
if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("ANTHROPIC_API_KEY is empty: paste your key into the .env file")

MODEL = "claude-fable-5-1"
EFFORT = "high"  # low | medium | high | xhigh | max

client = anthropic.Anthropic()


def ask(messages: list) -> None:
    """Stream one assistant turn and append it to the history."""
    # Thinking is always on for Fable 5.1, so there is no `thinking` param.
    # `fallbacks="default"` re-runs a safety-declined request on Anthropic's
    # recommended fallback model instead of just returning a refusal.
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=64000,
        output_config={"effort": EFFORT},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
        response = stream.get_final_message()
    print()

    if response.stop_reason == "refusal":
        category = response.stop_details.category if response.stop_details else None
        print(f"[refused, category: {category}]")
    elif response.stop_reason == "max_tokens":
        print("[cut off: hit max_tokens]")
    if response.model != MODEL:
        print(f"[served by fallback model {response.model}]")

    # Append the full content, not just the text: thinking blocks must be sent back unchanged.
    messages.append({"role": "assistant", "content": response.content})
    u = response.usage
    print(f"[tokens: {u.input_tokens} in / {u.output_tokens} out]\n")


def main() -> None:
    messages: list = []
    if len(sys.argv) > 1:
        messages.append({"role": "user", "content": " ".join(sys.argv[1:])})
        ask(messages)
        return

    print(f"Chatting with {MODEL} (effort={EFFORT}). Ctrl+C or blank line to quit.\n")
    while True:
        try:
            prompt = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not prompt:
            break
        messages.append({"role": "user", "content": prompt})
        print("claude> ", end="")
        try:
            ask(messages)
        except anthropic.AuthenticationError:
            sys.exit("Invalid API key: check ANTHROPIC_API_KEY in .env")
        except anthropic.RateLimitError:
            messages.pop()
            print("[rate limited, wait a moment and try again]\n")
        except anthropic.BadRequestError as e:
            messages.pop()
            print(f"[bad request: {e.message}]\n")
        except anthropic.APIConnectionError:
            messages.pop()
            print("[network error, try again]\n")


if __name__ == "__main__":
    main()
