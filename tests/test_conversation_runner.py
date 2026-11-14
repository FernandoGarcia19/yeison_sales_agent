"""The conversation test runner (Y-55): its checks, and every case file can be read."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("run_conversations", ROOT / "tools" / "run_conversations.py")
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

VIEW = {
    "state": "fulfillment_coord",
    "cart": [{"name": "Medias pack x3", "quantity": 2, "price": "35.00"}],
    "checkout_data": {"Nombre completo": "Luis"},
    "actions": [],
    "payment": {"method": None},
}


def test_a_matching_turn_has_no_problems():
    expect = {"state": "fulfillment_coord", "cart": {"Medias pack x3": 2}, "collected": ["Nombre completo"],
              "says": ["bs 70"], "supervisor": False}
    assert runner.check_turn(expect, VIEW, ["Son *Bs 70.00* en total"], [], ["$"]) == []


def test_each_check_reports_what_failed():
    expect = {"state": ["browsing", "cart_building"], "cart": {}, "cart_max": {"Medias pack x3": 1},
              "collected": ["NIT"], "actions": ["approve"], "supervisor": True, "says": ["gracias"],
              "not_says": ["confirmado"], "not_matches": [r"hacemos env[ií]os"]}
    problems = runner.check_turn(expect, VIEW, ["Pedido confirmado, hacemos envíos, cuesta $70"], [], ["$"])
    joined = "\n".join(problems)
    for piece in ("estado", "carrito {", "más que 1", "'NIT'", "acciones", "supervisor", "'gracias'",
                  "'confirmado'", "hacemos env", "'$'"):
        assert piece in joined, piece


def test_no_reply_is_a_problem_unless_expected():
    assert "el agente no respondió" in runner.check_turn({}, VIEW, [], [], [])
    assert runner.check_turn({"reply": False}, VIEW, [], [], []) == []


def test_supervisor_events_are_seen():
    events = [{"from": "supervisor_telegram", "text": "Nueva venta"}]
    assert runner.check_turn({"supervisor": True}, VIEW, ["ok"], events, []) == []


@pytest.mark.parametrize("path", sorted((ROOT / "tests" / "conversations").glob("[!_]*.toml")), ids=lambda p: p.name)
def test_every_case_file_is_valid(path):
    defaults = {"never": ["$"]}
    case = runner.Case.load(path, defaults)
    assert case.name and case.profile in {"demo", "moda"} and case.turns
    for turn in case.turns:
        assert turn.get("say") or turn.get("say_many") or turn.get("action") in {"receipt", "pay", "approve", "reject"}
        unknown = set(turn.get("expect") or {}) - {
            "state", "cart", "cart_has", "cart_max", "cart_without", "collected", "actions", "payment",
            "supervisor", "says", "says_any", "not_says", "not_matches", "reply"}
        assert not unknown, unknown
