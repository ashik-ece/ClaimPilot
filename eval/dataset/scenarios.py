"""Ground-truth claim generation. Labels come first; documents are rendered from them."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from dataset.policies import SPECS, build_policy

SEED = 20261023
TODAY = date(2026, 10, 23)
LATEST_INCIDENT = date(2026, 9, 30)

# Repair amounts in the seed data are scaled down to realistic collision-repair sizes.
AMOUNT_SCALE = 0.15
INFLATED_MULTIPLE = 2.0  # amount above this multiple of the typical claim is an outlier

# Number of claims per scenario type (sums to 200).
PLAN: dict[str, int] = {
    "approve_clean": 100,
    "approve_distractor": 16,
    "deny_exclusion": 30,
    "deny_late_notice": 8,
    "deny_outside_period": 6,
    "escalate_high_value": 10,
    "escalate_ambiguous": 14,
    "fraud_duplicate": 6,
    "fraud_inflated": 10,
}
DEGRADATIONS = {"scan_noise": 8, "rotated": 6, "missing_fields": 6}

FIRST = [
    "Maria",
    "James",
    "Priya",
    "Daniel",
    "Aisha",
    "Robert",
    "Linh",
    "Carlos",
    "Emily",
    "Tariq",
    "Hannah",
    "Marcus",
    "Sofia",
    "Kevin",
    "Grace",
    "Omar",
    "Rachel",
    "Victor",
    "Naomi",
    "Ethan",
    "Fatima",
    "Brian",
    "Chloe",
    "Andre",
    "Megan",
    "Samuel",
    "Yuki",
    "Patrick",
    "Leila",
    "Jordan",
]
LAST = [
    "Alvarez",
    "Brennan",
    "Chen",
    "Dawson",
    "Egan",
    "Fischer",
    "Gupta",
    "Hayes",
    "Ibrahim",
    "Jensen",
    "Kowalski",
    "Lopez",
    "Mitchell",
    "Nguyen",
    "Okafor",
    "Patel",
    "Quinn",
    "Reyes",
    "Schmidt",
    "Thompson",
    "Underwood",
    "Vasquez",
    "Walker",
    "Xu",
    "Young",
    "Zimmerman",
]
CITIES = {
    "OH": ["Columbus", "Dayton", "Toledo", "Akron"],
    "IL": ["Springfield", "Peoria", "Rockford", "Naperville"],
    "IN": ["Indianapolis", "Fort Wayne", "Evansville", "Bloomington"],
}
STREETS = [
    "Maple Street",
    "Oak Avenue",
    "Cedar Road",
    "Elm Drive",
    "Lake Boulevard",
    "Highland Avenue",
    "Mill Road",
    "Church Street",
    "River Road",
    "Park Avenue",
    "Sunset Drive",
    "Washington Street",
]
SHOPS = [
    "Midwest Collision Center",
    "Precision Auto Body",
    "Lakeside Autoworks",
    "Heartland Repair Group",
    "Summit Collision",
    "Eagle Body & Paint",
]

ZONE_ITEMS = {
    "Front Collision": [
        "Front bumper cover",
        "Headlamp assembly",
        "Hood panel",
        "Radiator support",
        "Front grille",
        "Fender",
    ],
    "Rear Collision": [
        "Rear bumper cover",
        "Trunk lid",
        "Tail lamp assembly",
        "Rear body panel",
        "Quarter panel",
        "Rear reinforcement bar",
    ],
    "Side Collision": [
        "Front door panel",
        "Rear door panel",
        "Quarter panel",
        "Side mirror",
        "Rocker panel",
        "Door glass",
    ],
}
LABOR_ITEMS = ["Body labor", "Paint and materials", "Frame and alignment", "Shop supplies"]

# Exclusion key -> cue shown in the incident narrative. `pre` precedes the event description;
# `event` (when set) replaces it. `{obj}` is filled in per claim.
CLEAR_CUES: dict[str, dict[str, str]] = {
    "racing": {
        "pre": "Earlier, another driver pulled alongside me and we began racing each other "
        "down the road, and I was well over the speed limit when I lost control."
    },
    "intentional": {
        "event": "I was furious after an argument and deliberately steered the front of "
        "my car into the {obj} on purpose."
    },
    "commercial_use": {
        "pre": "I was in the middle of a paid rideshare trip with a passenger in the back seat when this happened."
    },
    "unlicensed_driver": {
        "pre": "My nephew was driving that day, and I knew his license had been suspended since the spring."
    },
    "dui": {
        "pre": "I had been drinking at a bar for a couple of hours before I decided to drive "
        "home, and I was over the legal limit."
    },
    "wear_and_tear": {
        "pre": "The panels had been rusting and cracking from age for years, and "
        "I finally decided to put in a claim for all of the deterioration."
    },
    "off_road": {"pre": "I had turned off the paved road onto a dirt trail through a field to take a shortcut."},
    "modifications": {
        "pre": "My car has a turbo kit and an exhaust that I installed myself and never reported to the insurer."
    },
}
AMBIGUOUS_CUES: dict[str, str] = {
    "racing": "Another car was driving fast near me and I may have sped up a little, though I am "
    "not sure whether you would call that racing.",
    "intentional": "I swerved at the last moment, and afterward the other driver said I did it on "
    "purpose, which I dispute.",
    "commercial_use": "I was carrying some boxes to a friend's shop and he said he might "
    "give me gas money, I am not sure if that counts as work.",
    "unlicensed_driver": "My cousin was driving and thinks his license may have expired "
    "around last month, but he has not checked.",
    "dui": "I had one drink with dinner a few hours earlier and I felt fine, but the officer "
    "asked me a few questions about it.",
    "wear_and_tear": "The bumper already had some scuffs and surface rust, but the dent in it "
    "is definitely from this impact.",
    "off_road": "I think my tires touched the gravel shoulder just before the impact, but I "
    "am not certain if the shoulder counts as off-road.",
    "modifications": "A shop fitted a larger spoiler a while ago, and I do not know whether "
    "that counts as a modification that I should have reported.",
}
BENIGN_CUES = [
    "I was on my way to my regular job at the warehouse when this happened.",
    "I had just picked up a friend, unpaid, to give them a ride to the airport.",
    "I had renewed my driver's license the previous month and it was valid.",
    "It was raining heavily and the road surface was slick.",
    "I had just left the grocery store and was heading home.",
]

OBJECTS = ["guardrail", "utility pole", "concrete barrier", "retaining wall", "large tree"]
REAR_OBJECTS = ["concrete bollard", "fence post", "steel pole"]


@dataclass(frozen=True)
class Seed:
    stats: dict  # type: ignore[type-arg]

    @property
    def typical_amount(self) -> float:
        return float(self.stats["vehicle_claim_quantiles"]["all"][2]) * AMOUNT_SCALE


def load_seed(data_dir: Path) -> Seed:
    return Seed(json.loads((data_dir / "seed_stats.json").read_text()))


def _weighted(rng: random.Random, shares: dict[str, float]) -> str:
    keys = sorted(shares)
    return rng.choices(keys, weights=[shares[k] for k in keys])[0]


def _money(x: int) -> str:
    return f"{x}.00"


def _split_amount(rng: random.Random, total: int, zone: str) -> list[dict[str, str]]:
    n_parts = rng.randint(2, 4)
    parts = rng.sample(ZONE_ITEMS[zone], n_parts)
    labor = rng.sample(LABOR_ITEMS, rng.randint(1, 2))
    names = parts + labor
    weights = [rng.uniform(0.5, 3.0) for _ in names]
    amounts = [max(25, round(total * w / sum(weights))) for w in weights]
    amounts[-1] += total - sum(amounts)
    if amounts[-1] < 25:  # extremely rare: rebalance into the largest line
        big = amounts.index(max(amounts))
        amounts[big] += amounts[-1] - 25
        amounts[-1] = 25
    assert sum(amounts) == total and all(a > 0 for a in amounts)
    return [{"description": d, "amount": _money(a)} for d, a in zip(names, amounts, strict=True)]


def _sample_amount(rng: random.Random, seed: Seed, floor: int, ceiling: float) -> int:
    quant = seed.stats["vehicle_claim_quantiles"]["all"]
    lo, hi = quant[0], quant[-1]
    for _ in range(200):
        base = rng.triangular(lo, hi * 1.25, quant[2]) * AMOUNT_SCALE
        amount = round(base / 10) * 10
        if floor <= amount <= ceiling:
            return int(amount)
    return int(max(floor, min(ceiling, seed.typical_amount)))


def _make_vehicle(rng: random.Random, seed: Seed) -> tuple[int, str, str]:
    make, model = rng.choice(seed.stats["vehicles"])
    return rng.randint(2008, 2025), make, model


def _address(rng: random.Random, state: str) -> str:
    return f"{rng.randint(100, 9899)} {rng.choice(STREETS)}, {rng.choice(CITIES[state])}, {state}"


def _base_claim(rng: random.Random, seed: Seed, code: str) -> dict:  # type: ignore[type-arg]
    spec = SPECS[code]
    state = rng.choice(["OH", "IL", "IN"])
    year, make, model = _make_vehicle(rng, seed)
    incident_type = _weighted(rng, seed.stats["incident_type_share"])
    zone = _weighted(rng, seed.stats["collision_type_share"])
    latest = min(spec.end, LATEST_INCIDENT)
    span = (latest - spec.start).days
    incident = spec.start + timedelta(days=rng.randint(0, span))
    reported = incident + timedelta(days=rng.randint(1, max(1, min(spec.notice_days - 2, 10))))
    return {
        "policy_code": code,
        "state": state,
        "zone": zone,
        "has_police": rng.random() < 0.45,
        "other_object": rng.choice(REAR_OBJECTS if zone == "Rear Collision" else OBJECTS),
        "side": rng.choice(["left", "right"]),
        "fields": {
            "claimant_name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
            "policy_number": f"{spec.policy_number}-{rng.randint(100000, 999999)}",
            "incident_date": incident.isoformat(),
            "reported_date": min(reported, TODAY).isoformat(),
            "incident_type": incident_type,
            "incident_location": _address(rng, state),
            "vehicle_year": year,
            "vehicle_make": make,
            "vehicle_model": model,
            "police_report_number": None,
            "total_amount": None,
            "itemized_damages": [],
        },
        "scenario": {"type": "", "exclusion": None, "ambiguity": None, "distractor": None},
        "decision": "",
        "governing_clause_ids": [],
        "fraud_flags": [],
        "duplicate_of": None,
        "doc_quality": {"tier": "clean", "degradation": None},
    }


def _set_amount(c: dict, rng: random.Random, amount: int) -> None:  # type: ignore[type-arg]
    c["fields"]["total_amount"] = _money(amount)
    c["fields"]["itemized_damages"] = _split_amount(rng, amount, c["zone"])


def _police_number(rng: random.Random, c: dict) -> str:  # type: ignore[type-arg]
    return f"PR-{c['state']}-{c['fields']['incident_date'][:4]}-{rng.randint(10000, 99999)}"


def adjudicate(c: dict, policy: dict) -> tuple[str, list[str], list[str]]:  # type: ignore[type-arg]
    """Independent rule engine over the ground truth; must agree with every label."""
    f, roles, sc = c["fields"], policy["roles"], c["scenario"]
    inc = date.fromisoformat(f["incident_date"])
    rep = date.fromisoformat(f["reported_date"])
    amount = float(f["total_amount"])
    typical = c.get("_typical")
    flags: list[str] = []
    if not (date.fromisoformat(policy["effective_date"]) <= inc <= date.fromisoformat(policy["expiry_date"])):
        return "deny", [roles["period"]], ["date_outside_policy_period"]
    if (rep - inc).days > policy["notice_days"]:
        return "deny", [roles["notice"]], flags
    if sc["exclusion"] in policy["exclusions"]:
        return "deny", [roles[f"excl_{sc['exclusion']}"]], flags
    if c["duplicate_of"]:
        return "escalate", [roles["duplicate"]], ["duplicate_claim"]
    # Mutually exclusive with high-value: at or above the threshold, size alone is the reason.
    if typical is not None and INFLATED_MULTIPLE * typical < amount < policy["high_value_threshold"]:
        return "escalate", [roles["inflated"]], ["inflated_estimate"]
    if amount >= policy["high_value_threshold"]:
        return "escalate", [roles["high_value"]], flags
    if sc["ambiguity"] in policy["exclusions"]:
        return "escalate", [roles["unclear"], roles[f"excl_{sc['ambiguity']}"]], flags
    return "approve", [roles["coverage_grant"]], flags


def generate(data_dir: Path) -> list[dict]:  # type: ignore[type-arg]
    rng = random.Random(SEED)
    seed = load_seed(data_dir)
    policies = {code: build_policy(spec).to_json() for code, spec in SPECS.items()}
    codes = sorted(SPECS)
    typical = seed.typical_amount
    claims: list[dict] = []  # type: ignore[type-arg]

    def new(code: str | None = None) -> dict:  # type: ignore[type-arg]
        c = _base_claim(rng, seed, code or rng.choice(codes))
        c["_typical"] = typical
        return c

    def finish(c: dict, kind: str) -> None:  # type: ignore[type-arg]
        c["scenario"]["type"] = kind
        if c["has_police"]:
            c["fields"]["police_report_number"] = _police_number(rng, c)
        pol = policies[c["policy_code"]]
        decision, governing, flags = adjudicate(c, pol)
        c["decision"], c["governing_clause_ids"], c["fraud_flags"] = decision, governing, flags
        claims.append(c)

    def normal_amount(c: dict) -> int:  # type: ignore[type-arg]
        spec = SPECS[c["policy_code"]]
        ceiling = min(INFLATED_MULTIPLE * typical - 1, spec.high_value_threshold * 0.9)
        return _sample_amount(rng, seed, spec.deductible + 300, ceiling)

    for _ in range(PLAN["approve_clean"]):
        c = new()
        _set_amount(c, rng, normal_amount(c))
        finish(c, "approve_clean")

    for _ in range(PLAN["approve_distractor"]):
        c = new()
        _set_amount(c, rng, normal_amount(c))
        spec = SPECS[c["policy_code"]]
        absent = sorted(set(CLEAR_CUES) - set(spec.exclusions) - {"intentional"})
        if absent and rng.random() < 0.6:
            # An exclusion that exists in other policies but not in this one.
            c["scenario"]["exclusion"] = rng.choice(absent)
            c["scenario"]["distractor"] = f"exclusion_not_in_policy:{c['scenario']['exclusion']}"
        else:
            c["scenario"]["distractor"] = "benign_context"
        finish(c, "approve_distractor")

    for _ in range(PLAN["deny_exclusion"]):
        c = new()
        _set_amount(c, rng, normal_amount(c))
        c["scenario"]["exclusion"] = rng.choice(list(SPECS[c["policy_code"]].exclusions))
        if c["scenario"]["exclusion"] in ("dui", "unlicensed_driver", "racing"):
            c["has_police"] = True
        finish(c, "deny_exclusion")

    for _ in range(PLAN["deny_late_notice"]):
        c = new()
        spec = SPECS[c["policy_code"]]
        incident = date.fromisoformat(c["fields"]["incident_date"])
        late = spec.notice_days + rng.randint(3, 20)
        incident = min(incident, TODAY - timedelta(days=late + 1))
        incident = max(incident, spec.start)
        c["fields"]["incident_date"] = incident.isoformat()
        c["fields"]["reported_date"] = min(incident + timedelta(days=late), TODAY).isoformat()
        _set_amount(c, rng, normal_amount(c))
        finish(c, "deny_late_notice")

    for _ in range(PLAN["deny_outside_period"]):
        c = new()
        spec = SPECS[c["policy_code"]]
        incident = spec.start - timedelta(days=rng.randint(7, 60))
        c["fields"]["incident_date"] = incident.isoformat()
        c["fields"]["reported_date"] = (incident + timedelta(days=rng.randint(2, 9))).isoformat()
        _set_amount(c, rng, normal_amount(c))
        finish(c, "deny_outside_period")

    for _ in range(PLAN["escalate_high_value"]):
        c = new()
        spec = SPECS[c["policy_code"]]
        _set_amount(c, rng, rng.randint(spec.high_value_threshold, int(spec.limit * 0.9)) // 10 * 10)
        finish(c, "escalate_high_value")

    for _ in range(PLAN["escalate_ambiguous"]):
        c = new()
        _set_amount(c, rng, normal_amount(c))
        c["scenario"]["ambiguity"] = rng.choice(list(SPECS[c["policy_code"]].exclusions))
        finish(c, "escalate_ambiguous")

    for _ in range(PLAN["fraud_inflated"]):
        c = new(rng.choice(["STD", "PLUS", "PREM"]))
        spec = SPECS[c["policy_code"]]
        hi = min(typical * 3.5, spec.high_value_threshold * 0.95)
        _set_amount(c, rng, int(rng.uniform(typical * 2.6, hi)) // 10 * 10)
        finish(c, "fraud_inflated")

    # Duplicates reuse an earlier clean approval's identity, vehicle and incident.
    originals = rng.sample([c for c in claims if c["scenario"]["type"] == "approve_clean"], PLAN["fraud_duplicate"])
    for orig in originals:
        c = new(orig["policy_code"])
        for key in (
            "claimant_name",
            "policy_number",
            "incident_date",
            "incident_location",
            "vehicle_year",
            "vehicle_make",
            "vehicle_model",
            "incident_type",
        ):
            c["fields"][key] = orig["fields"][key]
        c["state"], c["zone"], c["other_object"] = orig["state"], orig["zone"], orig["other_object"]
        c["has_police"] = orig["has_police"]
        rep = date.fromisoformat(orig["fields"]["reported_date"]) + timedelta(days=rng.randint(2, 6))
        c["fields"]["reported_date"] = min(rep, TODAY).isoformat()
        _set_amount(c, rng, normal_amount(c))
        c["duplicate_of"] = f"orig:{id(orig)}"  # resolved to a claim id below
        c["_orig"] = orig
        finish(c, "fraud_duplicate")

    # Shuffle, then make sure every duplicate comes after the claim it duplicates.
    rng.shuffle(claims)
    for c in claims:
        orig = c.get("_orig")
        if orig is not None and claims.index(orig) > claims.index(c):
            i, j = claims.index(orig), claims.index(c)
            claims[i], claims[j] = claims[j], claims[i]
    for i, c in enumerate(claims, start=1):
        c["claim_id"] = f"CLM-{i:04d}"
        c["pair_id"] = c["claim_id"]  # duplicates share their original's pair_id (same split)
    for c in claims:
        if c.get("_orig") is not None:
            c["duplicate_of"] = c["_orig"]["claim_id"]
            c["pair_id"] = c["_orig"]["claim_id"]

    # Degraded document tiers on a random subset.
    pool = rng.sample(claims, sum(DEGRADATIONS.values()))
    it = iter(pool)
    for kind, count in DEGRADATIONS.items():
        for _ in range(count):
            next(it)["doc_quality"] = {"tier": "degraded", "degradation": kind}
    for c in claims:
        if c["doc_quality"]["degradation"] == "missing_fields":
            c["fields"]["vehicle_model"] = None
            c["fields"]["police_report_number"] = None

    # Stable output: drop generator-internal keys and validate labels once more.
    for c in claims:
        c.pop("_orig", None)
        c.pop("_typical", None)
    for c in claims:
        c["_typical"] = typical
        decision, governing, flags = adjudicate(c, policies[c["policy_code"]])
        assert (decision, governing, flags) == (
            c["decision"],
            c["governing_clause_ids"],
            c["fraud_flags"],
        ), c["claim_id"]
        c.pop("_typical")
    return claims
