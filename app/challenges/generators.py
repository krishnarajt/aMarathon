"""
Target generators — "what's today's number".

Each generator is a pure function `(challenge, period_index, period_start) ->
float`, registered under a `Generator` value. To add a training plan later,
write the function and register it; nothing else changes.
"""

import hashlib
import random
from datetime import datetime

from app.enums import Generator, Metric

# Round targets so they read like something a human picked: 8,400 steps, 6.5 km.
# Override per challenge with generator_config {"round_to": n}.
DEFAULT_ROUND_TO = {
    Metric.STEPS.value: 100.0,
    Metric.DISTANCE.value: 0.1,
}


def _round_to(value: float, step: float) -> float:
    if not step:
        return value
    return round(round(value / step) * step, 6)


def _rng(challenge, period_index: int) -> random.Random:
    """Seeded from the challenge uuid + period index.

    Deterministic on purpose: re-running generation for a period that somehow
    lost its row produces the number it had before, and it makes the generator
    testable without mocking randomness.
    """
    seed_material = f"{challenge.challenge_uuid}:{period_index}".encode()
    seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
    return random.Random(seed)


def random_target(challenge, period_index: int, period_start: datetime) -> float:
    """Uniform draw from the challenge's [target_min, target_max]."""
    low = float(challenge.target_min)
    high = float(challenge.target_max)
    if low > high:
        low, high = high, low

    config = challenge.generator_config or {}
    step = config.get("round_to", DEFAULT_ROUND_TO.get(challenge.metric, 1.0))

    value = _round_to(_rng(challenge, period_index).uniform(low, high), step)
    # Rounding can nudge the value past an endpoint.
    return min(max(value, low), high)


GENERATORS = {
    Generator.RANDOM.value: random_target,
}


def get_generator(name: str):
    if name not in GENERATORS:
        raise ValueError(
            f"Unknown target generator {name!r}. Known: {sorted(GENERATORS)}"
        )
    return GENERATORS[name]


def generate_target(challenge, period_index: int, period_start: datetime) -> float:
    return get_generator(challenge.generator)(challenge, period_index, period_start)
