"""
Winner rules — "who won today".

Each rule is a pure function `(entries, target, config) -> list[entries]` (a
list because ties are normal), registered under a `WinRule` value. Returning an
empty list means nobody won that period.

The rule applied to a period is the one snapshotted on the period row, not the
challenge's current setting — see ChallengePeriod's docstring.
"""

from app.challenges.periods import as_utc
from app.enums import WinRule


def highest_value(entries, target, config=None):
    """Biggest number wins, target or no target."""
    if not entries:
        return []
    best = max(entry.value for entry in entries)
    return [entry for entry in entries if entry.value == best]


def highest_above_target(entries, target, config=None):
    """Must hit the target; of those who did, the biggest number wins."""
    qualified = [entry for entry in entries if entry.value >= target]
    return highest_value(qualified, target)


def all_who_hit_target(entries, target, config=None):
    """Everyone who hit the target wins."""
    return [entry for entry in entries if entry.value >= target]


def first_to_target(entries, target, config=None):
    """Earliest to cross the target wins.

    Uses `target_reached_at`, which is stamped once when an entry first reaches
    the target, so editing a value upwards later can't backdate someone into
    first place.
    """
    crossed = [
        entry
        for entry in entries
        if entry.target_reached_at is not None and entry.value >= target
    ]
    if not crossed:
        return []
    earliest = min(as_utc(entry.target_reached_at) for entry in crossed)
    return [
        entry for entry in crossed if as_utc(entry.target_reached_at) == earliest
    ]


WIN_RULES = {
    WinRule.HIGHEST_VALUE.value: highest_value,
    WinRule.HIGHEST_ABOVE_TARGET.value: highest_above_target,
    WinRule.ALL_WHO_HIT_TARGET.value: all_who_hit_target,
    WinRule.FIRST_TO_TARGET.value: first_to_target,
}


def get_win_rule(name: str):
    if name not in WIN_RULES:
        raise ValueError(f"Unknown win rule {name!r}. Known: {sorted(WIN_RULES)}")
    return WIN_RULES[name]


def pick_winners(period, entries):
    """Apply the period's snapshotted rule to its entries."""
    rule = get_win_rule(period.win_rule)
    return rule(entries, period.target_value, period.win_rule_config or {})
