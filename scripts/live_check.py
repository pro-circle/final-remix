"""Live smoke check against the real providers.

Reads keys from .env in the current folder (GROQ_API_KEY…_5, GEMINI_API_KEY…_5),
sends one tiny request to every model in the routing chains that has keys, and
prints which key served it. No project files are touched.

    python3 scripts/live_check.py
"""

from __future__ import annotations

import sys

from forge.config import load_config, provider_for
from forge.models.client import FleetClient, ModelError
from forge.models.router import PHASE_ROLE, ModelRouter


def main() -> int:
    cfg = load_config()
    print(f"groq keys: {len(cfg.api_keys)}   gemini keys: {len(cfg.gemini_keys)}")
    if not cfg.all_keys:
        print("No keys in .env - nothing to check.")
        return 1

    fleet = FleetClient(cfg)
    router = ModelRouter(cfg, fleet)
    failures = 0
    try:
        print("\nphase -> model chosen by live capacity")
        for phase in PHASE_ROLE:
            chain = cfg.candidates(phase, PHASE_ROLE[phase])
            print(f"  {phase:<10} {router.choose(phase, need=2000):<22} chain={chain}")

        models = sorted({m for p in PHASE_ROLE for m in cfg.candidates(p, PHASE_ROLE[p])})
        print("\none real request per model")
        for model in models:
            if not fleet.serves(model):
                print(f"  {model:<22} skipped (no {provider_for(model)} keys)")
                continue
            try:
                out = fleet.chat(
                    model=model,
                    messages=[{"role": "user", "content": "Reply with the single word: ready"}],
                )
                print(f"  {model:<22} ok via {provider_for(model)}: {out.content.strip()[:40]!r}")
            except ModelError as exc:
                failures += 1
                print(f"  {model:<22} FAILED: {exc}")

        print("\nkey health")
        for row in fleet.keys.health():
            print(
                f"  {row['provider']:<7} {row['label']:<22} ok={row['successes']} "
                f"fails={row['failures']} cooldown={row['cooldown_s']}s {row['last_error']}"
            )
        print(
            f"\ntokens in/out: {fleet.usage.tokens_in}/{fleet.usage.tokens_out} "
            f"over {fleet.usage.requests} requests"
        )
    finally:
        fleet.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
