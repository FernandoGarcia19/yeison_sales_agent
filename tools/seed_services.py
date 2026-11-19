"""
The service and the plan the conversation cases 50-53 sell (Y-103), created in the «moda» test account.

    python tools/seed_services.py --env staging      # or --env local

It logs in with the «moda» profile of tools/conversations.local.toml and creates, through the panel API,
whatever of these is missing (by name): «Asesoría de estilo» (a service, no stock) and «Club Moda Andina
mensual» (a plan). Running it again changes nothing. Only the standard library is used.
"""

import argparse
import json
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_conversations import LOCAL_CONFIG, Panel  # noqa: E402

ITEMS = [
    {
        "name": "Asesoría de estilo", "kind": "service", "price": "150", "duration_minutes": 60,
        "description": "Sesión personal con nuestra estilista.",
        "details": {
            "fields": [
                {"key": "duration", "label": "Duración", "value": "60 minutos"},
                {"key": "modality", "label": "Modalidad", "value": "En el local o virtual"},
                {"key": "includes", "label": "Qué incluye",
                 "value": "Revisión de tu armario, 3 looks armados con prendas de la tienda y una lista de compras"},
                {"key": "excludes", "label": "Qué no incluye", "value": "Las prendas que compres"},
                {"key": "cancellation", "label": "Política de cancelación",
                 "value": "Puedes reprogramar avisando con 24 horas de anticipación"},
            ],
            "faq": [{"q": "¿Puedo llevar a una amiga?", "a": "Sí, sin costo extra."}],
        },
    },
    {
        "name": "Club Moda Andina mensual", "kind": "plan", "price": "99",
        "description": "Membresía mensual de la tienda.",
        "details": {
            "fields": [
                {"key": "period", "label": "Periodicidad", "value": "Mensual"},
                {"key": "includes", "label": "Qué incluye",
                 "value": "10 % de descuento en toda la tienda y un ajuste de prenda gratis al mes"},
                {"key": "trial", "label": "Prueba gratis", "value": "No tiene prueba gratis"},
                {"key": "commitment", "label": "Permanencia", "value": "Sin permanencia: cancelas cuando quieras"},
            ],
            "faq": [{"q": "¿El descuento vale en ofertas?", "a": "No, solo en precios normales."}],
        },
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--env", default="staging")
    parser.add_argument("--config", type=Path, default=LOCAL_CONFIG)
    args = parser.parse_args()
    if not args.config.exists():
        print(f"Falta {args.config}: copia tools/conversations.example.toml y completa las cuentas.")
        return 2
    env = tomllib.loads(args.config.read_text(encoding="utf-8"))[args.env]
    account = env["agents"]["moda"]
    panel = Panel(env["api_url"])
    if account.get("token"):
        panel.tokens["moda"] = account["token"]
    else:
        panel.login("moda", account["email"], account["password"])
    auth = {"authorization": f"Bearer {panel.tokens['moda']}", "content-type": "application/json"}

    existing = {p["name"] for p in panel._request("GET", "/inventory/catalog", None, auth)}
    for item in ITEMS:
        if item["name"] in existing:
            print(f"  ya está: {item['name']}")
            continue
        body = json.dumps({**item, "track_inventory": False}).encode()
        created = panel._request("POST", "/inventory/catalog", body, auth)
        print(f"  creado: {item['name']} (id {created['id']}, {created.get('kind')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
