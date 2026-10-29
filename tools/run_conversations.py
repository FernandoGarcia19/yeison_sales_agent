"""
Conversation tests of the sales agent (Y-55).

Each case in tests/conversations/*.toml is a scripted chat that runs through the agent test chat
(panel `POST /api/v1/agents/{id}/simulator`): the real pipeline and model, nothing written or sent.
After every turn the script checks the state, the cart, the data collected, the actions offered and
the agent's words. The model varies, so each case runs several times.

    python tools/run_conversations.py --env staging            # every case, 3 runs each
    python tools/run_conversations.py --env staging -k carrito  # cases whose file or name matches
    python tools/run_conversations.py --env staging --runs 1 --verbose

Accounts and agents come from tools/conversations.local.toml (ignored by git; copy the example).
A case with `known_issue = "Y-56"` is expected to fail until that issue is done: it is reported
apart and does not fail the run; if it passes every time, the report says to remove the mark.
Only the standard library is used, so it runs with any Python 3.11+.
"""

import argparse
import json
import re
import secrets
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "tests" / "conversations"
LOCAL_CONFIG = Path(__file__).resolve().parent / "conversations.local.toml"
TURN_TIMEOUT = 120


# --- panel client ---------------------------------------------------------------------------

class Panel:
    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip("/")
        self.tokens: Dict[str, str] = {}

    def _request(self, method: str, path: str, body: Optional[bytes], headers: Dict[str, str]) -> Any:
        request = urllib.request.Request(self.api_url + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=TURN_TIMEOUT) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"HTTP {e.code} en {path}: {detail}") from None

    def login(self, profile: str, email: str, password: str) -> None:
        form = urllib.parse.urlencode({"username": email, "password": password}).encode()
        data = self._request("POST", "/auth/login", form, {"content-type": "application/x-www-form-urlencoded"})
        self.tokens[profile] = data["access_token"]

    def turn(self, profile: str, agent_id: int, session: str, action: str, message: Optional[str]) -> Dict[str, Any]:
        body = json.dumps({"session_id": session, "action": action, "message": message}).encode()
        headers = {"content-type": "application/json", "authorization": f"Bearer {self.tokens[profile]}"}
        return self._request("POST", f"/agents/{agent_id}/simulator", body, headers)


# --- checks ---------------------------------------------------------------------------------

def _norm(text: str) -> str:
    return (text or "").lower()


def _cart(view: Dict[str, Any]) -> Dict[str, int]:
    cart: Dict[str, int] = {}
    for item in view.get("cart") or []:
        cart[item["name"]] = cart.get(item["name"], 0) + int(item.get("quantity") or 0)
    return cart


def check_turn(expect: Dict[str, Any], view: Dict[str, Any], replies: List[str], events: List[Dict[str, Any]],
               never: List[str]) -> List[str]:
    """What did not hold after this turn (empty = the turn passed)."""
    problems = []
    said = "\n".join(replies)
    low = _norm(said)
    cart = _cart(view)

    if "state" in expect:
        allowed = expect["state"] if isinstance(expect["state"], list) else [expect["state"]]
        if view.get("state") not in allowed:
            problems.append(f"estado {view.get('state')!r}, se esperaba {' o '.join(allowed)}")
    if "cart" in expect and cart != expect["cart"]:
        problems.append(f"carrito {cart}, se esperaba {expect['cart']}")
    for name, qty in (expect.get("cart_has") or {}).items():
        if cart.get(name) != qty:
            problems.append(f"carrito sin {name} x{qty} (tiene {cart})")
    for name, top in (expect.get("cart_max") or {}).items():
        if cart.get(name, 0) > top:
            problems.append(f"carrito con {name} x{cart[name]}, más que {top}")
    for name in expect.get("cart_without") or []:
        if name in cart:
            problems.append(f"carrito todavía tiene {name}")
    for name in expect.get("collected") or []:
        if not (view.get("checkout_data") or {}).get(name):
            problems.append(f"no guardó el dato {name!r}")
    if "actions" in expect and sorted(view.get("actions") or []) != sorted(expect["actions"]):
        problems.append(f"acciones {view.get('actions')}, se esperaba {expect['actions']}")
    if "payment" in expect and (view.get("payment") or {}).get("method") != expect["payment"]:
        problems.append(f"pago {(view.get('payment') or {}).get('method')!r}, se esperaba {expect['payment']!r}")

    to_supervisor = [e for e in events if str(e.get("from", "")).startswith("supervisor")]
    if expect.get("supervisor") is True and not to_supervisor:
        problems.append("no avisó al supervisor")
    if expect.get("supervisor") is False and to_supervisor:
        problems.append("avisó al supervisor sin motivo")

    for words in expect.get("says") or []:
        if _norm(words) not in low:
            problems.append(f"no dijo {words!r}")
    for alternatives in expect.get("says_any") or []:
        if not any(_norm(w) in low for w in alternatives):
            problems.append(f"no dijo ninguna de {alternatives}")
    for words in (expect.get("not_says") or []) + never:
        if _norm(words) in low:
            problems.append(f"dijo {words!r}")
    for pattern in expect.get("not_matches") or []:
        if re.search(pattern, low):
            problems.append(f"dijo algo que coincide con /{pattern}/")
    if not replies and expect.get("reply", True):
        problems.append("el agente no respondió")
    return problems


# --- cases ----------------------------------------------------------------------------------

@dataclass
class Case:
    path: Path
    name: str
    profile: str
    turns: List[Dict[str, Any]]
    known_issue: Optional[str] = None
    never: List[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path, defaults: Dict[str, Any]) -> "Case":
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return cls(path=path, name=data["name"], profile=data["agent"], turns=data["turns"],
                   known_issue=data.get("known_issue"),
                   never=list(defaults.get("never", [])) + list(data.get("never", [])))


@dataclass
class TurnResult:
    label: str
    problems: List[str]
    replies: List[str]
    seconds: float


def run_case(panel: Panel, case: Case, agent_id: int) -> List[TurnResult]:
    session = secrets.token_hex(8)
    results: List[TurnResult] = []
    try:
        for index, turn in enumerate(case.turns, 1):
            action = turn.get("action", "message")
            message = turn.get("say")
            label = f"{index}. {message!r}" if message else f"{index}. [{action}]"
            started = time.monotonic()
            try:
                view = panel.turn(case.profile, agent_id, session, action, message)
            except RuntimeError as e:
                results.append(TurnResult(label, [str(e)], [], time.monotonic() - started))
                break
            # What this turn produced: the agent's messages, the supervisor's notices, stock exits
            events = list(view.get("events") or [])
            replies = [e["text"] for e in events if e.get("from") == "agent"]
            problems = check_turn(turn.get("expect") or {}, view, replies, events, case.never)
            if view.get("trace", {}).get("error"):
                problems.append(f"error del pipeline: {view['trace']['error']}")
            results.append(TurnResult(label, problems, replies, time.monotonic() - started))
            if problems and turn.get("stop_on_failure", True):
                break
    finally:
        try:
            panel.turn(case.profile, agent_id, session, "reset", None)
        except RuntimeError:
            pass
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Conversation tests of the sales agent (Y-55)")
    parser.add_argument("--env", default="staging", help="section of conversations.local.toml")
    parser.add_argument("--config", type=Path, default=LOCAL_CONFIG)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("-k", dest="filter", help="only cases whose file name or title contains this")
    parser.add_argument("--verbose", "-v", action="store_true", help="print the agent's replies")
    args = parser.parse_args()

    if not args.config.exists():
        print(f"Falta {args.config}: copia tools/conversations.example.toml y completa las cuentas.")
        return 2
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    env = config[args.env]
    defaults = tomllib.loads((CASES_DIR / "_defaults.toml").read_text(encoding="utf-8"))

    cases = [Case.load(p, defaults) for p in sorted(CASES_DIR.glob("*.toml")) if not p.name.startswith("_")]
    if args.filter:
        cases = [c for c in cases if args.filter.lower() in (c.path.name + c.name).lower()]

    panel = Panel(env["api_url"])
    for profile in sorted({c.profile for c in cases}):
        account = env["agents"][profile]
        panel.login(profile, account["email"], account["password"])

    failed, known, fixed = [], [], []
    for case in cases:
        agent_id = int(env["agents"][case.profile]["agent_id"])
        tag = f"  [{case.known_issue}]" if case.known_issue else ""
        print(f"\n▶ {case.name} ({case.path.name}){tag}")
        passes = 0
        for run in range(1, args.runs + 1):
            results = run_case(panel, case, agent_id)
            ok = len(results) == len(case.turns) and not any(r.problems for r in results)
            passes += ok
            seconds = sum(r.seconds for r in results)
            print(f"  corrida {run}: {'✅ pasa' if ok else '❌ falla'} ({seconds:.0f} s)")
            for r in results:
                if r.problems or args.verbose:
                    print(f"    {r.label}")
                    for p in r.problems:
                        print(f"      - {p}")
                    if args.verbose or r.problems:
                        for reply in r.replies:
                            print("      agente: " + reply.replace("\n", " ")[:300])
        summary = f"{case.name}: {passes}/{args.runs}"
        if case.known_issue:
            (fixed if passes == args.runs else known).append(f"{summary} ({case.known_issue})")
        elif passes < args.runs:
            failed.append(summary)

    print("\n=== Resumen ===")
    print(f"Casos: {len(cases)} · fallan: {len(failed)} · fallas conocidas: {len(known)} · conocidas que ya pasan: {len(fixed)}")
    for line in failed:
        print(f"  ❌ {line}")
    for line in known:
        print(f"  ⏳ {line}")
    for line in fixed:
        print(f"  🎉 {line}: ya pasa, quita known_issue del caso")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
