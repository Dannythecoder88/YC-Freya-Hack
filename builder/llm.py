"""Claude calls for the builder. Every call is written to the trace: model, purpose, tokens, seconds.

Each call has a wall-clock limit: the builder must finish within 30 minutes, and a slow answer is worth less than a
finished build. A call that runs over its limit is abandoned and raises CallTimeout.
"""
import os
import threading
import time

import anthropic

MODEL = os.environ.get("BUILDER_MODEL", "claude-opus-5-5")
_client = None


class CallTimeout(RuntimeError):
    pass


def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=3, timeout=600)
    return _client


def ask(trace, purpose, system, user, effort="high", max_tokens=64000, limit_s=420):
    """One streamed request. Returns the text of the answer."""
    t0 = time.time()
    trace.log(f"MODEL CALL start | model={MODEL} effort={effort} limit={limit_s}s | purpose: {purpose}")
    box = {}

    def run():
        try:
            with client().messages.stream(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": effort},
            ) as stream:
                box["msg"] = stream.get_final_message()
        except Exception as e:  # handed to the caller below
            box["err"] = e

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(limit_s)
    if th.is_alive():
        trace.log(f"MODEL CALL abandoned after {time.time() - t0:.0f} s (limit {limit_s} s) | purpose: {purpose}")
        raise CallTimeout(purpose)
    if "err" in box:
        raise box["err"]
    msg = box["msg"]
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"model refused: {getattr(msg, 'stop_details', None)}")
    text = "".join(b.text for b in msg.content if b.type == "text")
    u = msg.usage
    trace.log(f"MODEL CALL done  | model={MODEL} | {time.time() - t0:.0f} s | in={u.input_tokens} out={u.output_tokens} | stop={msg.stop_reason} | purpose: {purpose}")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("model answer was cut off at max_tokens")
    return text
