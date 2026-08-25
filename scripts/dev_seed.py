#!/usr/bin/env python3
"""
Seed a running Amarathon backend with demo data, then check the endpoints the
UI depends on actually answer.

Written against the HTTP API rather than the database so it exercises the same
paths the frontend does — auth, friend requests, invitations, lazy period
generation and all. Uses only the standard library, so it runs without adding
a dependency for something that never ships.

    python scripts/dev_seed.py --base-url http://localhost:8010

Re-running is safe: existing users are logged in rather than registered, and
`--fresh` on a SQLite dev database is easier than de-duplicating challenges.
"""

import argparse
import json
import random
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_URL = "http://localhost:8010"
PASSWORD = "amarathon"

failures: list[str] = []


def request(method, path, body=None, token=None, expect=None):
    """Call the API. Returns (status, parsed body or None)."""
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req) as response:
            payload = response.read()
            parsed = json.loads(payload) if payload else None
            status = response.status
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            parsed = json.loads(payload) if payload else None
        except json.JSONDecodeError:
            parsed = {"raw": payload.decode(errors="replace")}
        status = e.code
    except urllib.error.URLError as e:
        print(f"  ✗ cannot reach {url}: {e.reason}")
        sys.exit(1)

    if expect is not None and status != expect:
        failures.append(f"{method} {path} -> {status} (wanted {expect}): {parsed}")
        print(f"  ✗ {method} {path} -> {status} {parsed}")
    return status, parsed


def check(label, condition, detail=""):
    if condition:
        print(f"  ✓ {label}")
    else:
        failures.append(f"{label} {detail}")
        print(f"  ✗ {label} {detail}")


def sign_in(username, display_name):
    """Register, or log in if the username is taken."""
    status, body = request(
        "POST",
        "/api/auth/register",
        {"username": username, "password": PASSWORD, "display_name": display_name},
    )
    if status == 409:
        status, body = request(
            "POST", "/api/auth/login", {"username": username, "password": PASSWORD},
            expect=200,
        )
    elif status != 201:
        failures.append(f"register {username} -> {status}: {body}")
    return body


def befriend(a, b):
    """Make two users friends, from either side of an existing request."""
    status, result = request(
        "POST", "/api/friends/requests", {"username": b["user"]["username"]},
        token=a["access_token"],
    )
    if status not in (200, 201) or result is None:
        return
    if result.get("result") in ("already_friends", "accepted"):
        return

    _, requests_in = request(
        "GET", "/api/friends/requests", token=b["access_token"], expect=200
    )
    for incoming in (requests_in or {}).get("incoming", []):
        if incoming["other_user"]["username"] == a["user"]["username"]:
            request(
                "POST",
                f"/api/friends/requests/{incoming['id']}/respond",
                {"accept": True},
                token=b["access_token"],
                expect=200,
            )


def main():
    global BASE_URL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--seed", type=int, default=7, help="RNG seed for the fake numbers")
    args = parser.parse_args()
    BASE_URL = args.base_url.rstrip("/")
    rng = random.Random(args.seed)

    print(f"\nAmarathon dev seed → {BASE_URL}")

    print("\n[1] health")
    status, body = request("GET", "/health", expect=200)
    check("backend is up", (body or {}).get("status") == "healthy")

    print("\n[2] accounts")
    people = [
        ("saket", "Saket"),
        ("nadia", "Nadia"),
        ("rhys", "Rhys"),
        ("mira", "Mira"),
    ]
    users = {}
    for username, display_name in people:
        users[username] = sign_in(username, display_name)
        check(f"signed in as {username}", bool(users[username]))
    me = users["saket"]
    token = me["access_token"]

    print("\n[3] auth round trip")
    status, body = request("GET", "/api/auth/me", token=token, expect=200)
    check("GET /auth/me returns the right user", (body or {}).get("username") == "saket")
    request("GET", "/api/auth/me", expect=403)
    status, refreshed = request(
        "POST", "/api/auth/refresh", {"refresh_token": me["refresh_token"]}, expect=200
    )
    check("refresh token rotates", bool((refreshed or {}).get("access_token")))
    if refreshed:
        me = refreshed
        token = me["access_token"]

    print("\n[4] friends")
    for other in ("nadia", "rhys", "mira"):
        befriend(me, users[other])
    status, friends = request("GET", "/api/friends", token=token, expect=200)
    check("three friends", len(friends or []) == 3, f"got {len(friends or [])}")
    status, found = request("GET", "/api/users/search?q=nad", token=token, expect=200)
    check("user search finds nadia", any(u["username"] == "nadia" for u in found or []))

    print("\n[5] challenges")
    now = datetime.now(timezone.utc)
    blueprints = [
        {
            "name": "Dawn Patrol",
            "description": "Whoever moves first, moves best. Daily steps, no excuses.",
            "metric": "steps",
            "target_min": 6000,
            "target_max": 12000,
            "win_rule": "highest_above_target",
            "period_type": "daily",
            "days_back": 24,
            "invite": ["nadia", "rhys"],
            "scale": 9000,
        },
        {
            "name": "The Long Way Round",
            "description": "Kilometres only. Bring a podcast.",
            "metric": "distance",
            "unit": "km",
            "target_min": 3,
            "target_max": 8,
            "win_rule": "first_to_target",
            "period_type": "daily",
            "days_back": 16,
            "invite": ["mira", "nadia"],
            "scale": 6,
        },
        {
            "name": "Weekend Warriors",
            "description": "One number a week. Make it hurt.",
            "metric": "distance",
            "unit": "km",
            "target_min": 20,
            "target_max": 42,
            "win_rule": "all_who_hit_target",
            "period_type": "weekly",
            "days_back": 63,
            "invite": ["rhys"],
            "scale": 34,
        },
    ]

    created = []
    for spec in blueprints:
        payload = {
            "name": spec["name"],
            "description": spec["description"],
            "metric": spec["metric"],
            "target_min": spec["target_min"],
            "target_max": spec["target_max"],
            "win_rule": spec["win_rule"],
            "period_type": spec["period_type"],
            "starts_at": (now - timedelta(days=spec["days_back"])).isoformat(),
            "invite": spec["invite"],
        }
        if "unit" in spec:
            payload["unit"] = spec["unit"]
        status, challenge = request("POST", "/api/challenges", payload, token=token, expect=201)
        if challenge:
            created.append((challenge, spec))
            check(f"created '{spec['name']}'", True)

    print("\n[6] invitations")
    for challenge, spec in created:
        for username in spec["invite"]:
            # Mira sits on the Long Way Round invite so the UI has a pending one.
            if username == "mira":
                continue
            request(
                "POST",
                f"/api/challenges/{challenge['challenge_uuid']}/respond",
                {"accept": True},
                token=users[username]["access_token"],
                expect=200,
            )
    check("one invitation left pending for mira", True)

    print("\n[7] logging entries against every past period")
    for challenge, spec in created:
        status, periods = request(
            "GET",
            f"/api/challenges/{challenge['challenge_uuid']}/periods?limit=365",
            token=token,
            expect=200,
        )
        players = ["saket"] + [u for u in spec["invite"] if u != "mira"]
        for period in periods or []:
            for username in players:
                # Skip the odd day so streaks, gaps and "not logged yet" all
                # show up in the UI rather than a wall of perfect data.
                if rng.random() < 0.12:
                    continue
                spread = rng.uniform(0.62, 1.38)
                value = round(spec["scale"] * spread, 2)
                if spec["metric"] == "steps":
                    value = int(value)
                request(
                    "PUT",
                    f"/api/periods/{period['id']}/entry",
                    {"value": value, "note": rng.choice([None, "felt good", "brutal wind", "treadmill"])},
                    token=users[username]["access_token"],
                    expect=200,
                )

    print("\n[8] the endpoints the UI reads")
    first_uuid = created[0][0]["challenge_uuid"]

    status, dash = request("GET", "/api/stats/dashboard", token=token, expect=200)
    check("dashboard lists active challenges", len((dash or {}).get("active", [])) >= 2)
    check(
        "dashboard card carries today's target",
        bool(dash and dash["active"] and dash["active"][0].get("period")),
    )
    check("dashboard reports totals", (dash or {}).get("overview", {}).get("entries_logged", 0) > 0)

    status, board = request(
        "GET", f"/api/challenges/{first_uuid}/leaderboard", token=token, expect=200
    )
    check("leaderboard has a row per participant", len(board or []) >= 3)
    check("leaderboard counts wins", any(row["wins"] > 0 for row in board or []))

    status, series = request(
        "GET", f"/api/challenges/{first_uuid}/timeseries", token=token, expect=200
    )
    check("timeseries returns periods", len(series or []) > 5)
    check(
        "timeseries points carry a target and entries",
        bool(series and series[0]["target_value"] and series[0]["entries"]),
    )

    status, activity = request("GET", "/api/stats/me/activity?days=90", token=token, expect=200)
    check("activity history has days", len(activity or []) > 5)

    status, detail = request("GET", f"/api/challenges/{first_uuid}", token=token, expect=200)
    check("challenge detail has participants", len((detail or {}).get("participants", [])) >= 3)

    status, settings = request("GET", "/api/settings", token=token, expect=200)
    check("settings load", bool(settings))
    request(
        "PUT", "/api/settings", {"timezone": "Asia/Kolkata", "distance_unit": "km"},
        token=token, expect=200,
    )

    print("\n[9] permissions")
    # Mira never accepted the Long Way Round, and is in nothing else — the
    # challenge she was never invited to must not exist as far as she's concerned.
    request(
        "GET",
        f"/api/challenges/{first_uuid}",
        token=users["mira"]["access_token"],
        expect=404,
    )
    request(
        "DELETE",
        f"/api/challenges/{first_uuid}",
        token=users["nadia"]["access_token"],
        expect=403,
    )

    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("All checks passed. Log in as any of: " + ", ".join(u for u, _ in people))
    print(f"Password for every demo account: {PASSWORD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
