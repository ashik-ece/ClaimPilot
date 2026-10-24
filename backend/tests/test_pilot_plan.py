from collections import Counter

from app.pilot import _load_labels, choose_pool, is_hard, plan

LABELS = _load_labels()


def test_pool_is_balanced_by_decision_and_difficulty() -> None:
    pool = choose_pool(LABELS, seed=1)
    assert len(pool) == len(set(pool)) == 12
    kinds = Counter((LABELS[i]["decision"], is_hard(LABELS[i])) for i in pool)
    assert set(kinds.values()) == {2} and len(kinds) == 6
    assert all(LABELS[i]["split"] == "test" for i in pool)


def test_second_round_uses_fresh_claims() -> None:
    first = choose_pool(LABELS, seed=1)
    second = choose_pool(LABELS, seed=2, exclude=first)
    assert not set(first) & set(second)


def test_pool_choice_is_reproducible() -> None:
    assert choose_pool(LABELS, seed=7) == choose_pool(LABELS, seed=7)


def test_every_tester_gets_six_and_six_and_every_claim_both_modes() -> None:
    pool = choose_pool(LABELS, seed=3)
    schedule = plan(pool, testers=4, seed=3)
    for tasks in schedule:
        assert sorted(c for c, _ in tasks) == pool
        assert Counter(m for _, m in tasks) == {"assisted": 6, "manual": 6}
    for claim in pool:
        modes = Counter(m for tasks in schedule for c, m in tasks if c == claim)
        assert modes == {"assisted": 2, "manual": 2}


def test_learning_claim_alternates_between_modes_across_testers() -> None:
    schedule = plan(choose_pool(LABELS, seed=3), testers=4, seed=3)
    assert [tasks[0][1] for tasks in schedule] == ["assisted", "manual", "assisted", "manual"]


def test_odd_tester_counts_still_give_each_tester_a_6_6_split() -> None:
    for tasks in plan(choose_pool(LABELS, seed=3), testers=3, seed=3):
        assert Counter(m for _, m in tasks) == {"assisted": 6, "manual": 6}
