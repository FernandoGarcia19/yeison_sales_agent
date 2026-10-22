# yeison_sales_agent

El vendedor de Yeison por WhatsApp. Recibe los mensajes de los clientes de cada negocio, consulta el inventario, arma el
carrito, cobra (QR o pago en persona) y le avisa al supervisor por Telegram, que aprueba o rechaza el comprobante. Al
aprobar, registra la venta y descuenta el stock. También recibe el webhook del bot de Telegram y le pasa al panel lo que
es para el Director.

FastAPI · LangGraph/LangChain · OpenAI (u OpenRouter) · Redis (agrupa mensajes y cachea) · SQLAlchemy async ·
Evolution API (WhatsApp) · Cloudflare R2 (comprobantes y QR). Python 3.11.

Puerto: 8000 al correrlo con `python main.py`, 8090 en el contenedor de producción y 8091 en el de staging. En la VPS,
nginx lo publica bajo `/agent/`.

## Cómo fluye un mensaje

```
WhatsApp → Evolution API → POST /api/v1/webhooks/evolution → Redis (agrupa 3 s o 5 mensajes) → pipeline → respuesta por Evolution
```

1. **Webhook** (`app/api/v1/webhooks.py`): exige el header `x-evolution-webhook-secret`; sin secreto o si no coincide,
   responde 401. Ignora los mensajes propios (`fromMe`), los grupos y los eventos que no son mensajes. El agente sale de la
   instancia de Evolution (`whatsapp_connection.evolution_instance_name`); `key.remoteJid` es el cliente. La media cifrada se
   baja con `/chat/getBase64FromMediaMessage`.
2. **Agrupado** (`app/services/batch_manager.py`): junta los mensajes de un cliente durante 3 s o hasta 5, y corre el pipeline una vez.
3. **Pipeline** (`app/services/pipeline/runner.py`, etapas en `stages/`):
   - validación;
   - identificación del negocio y del agente;
   - **tope del plan** (`usage_gate.py`, pregunta al panel);
   - clasificación;
   - contexto (configuración, inventario, conversación);
   - acción (carrito, checkout, pago);
   - respuesta.
4. **Estados de la conversación**: `browsing → cart_building → fulfillment_coord → awaiting_payment_method →
   awaiting_receipt → order_completed` (+ `paused` cuando la toma una persona).
5. **Pago**: con QR, el cliente manda el comprobante (se guarda en R2) y el supervisor aprueba o rechaza con los botones de
   Telegram; con pago en persona, la venta queda pendiente. Al aprobar, `app/services/sale_recorder.py` registra la venta
   en `sale` (QR → `paid`, en persona → `pending`) y el panel descuenta el stock (`/internal/inventory/sale-exit`).

Twilio sigue en el código como respaldo (`POST /api/v1/webhooks/twilio`, `WHATSAPP_PROVIDER_DEFAULT`), con sus credenciales por subcuenta.

### Modelos

`app/services/pipeline/llm_models.py`: la clasificación, la decisión y las extracciones devuelven un JSON corto y usan
`OPENAI_SMALL_MODEL` (en staging `gpt-5.4-nano`); la respuesta que lee el cliente usa `OPENAI_MODEL` (por defecto
`gpt-5.4-mini`). Cada llamada manda `prompt_cache_key=yeison-agent-<id>-<paso>` (`PROMPT_CACHE_ENABLED`) y escribe una fila
en `llm_usage` (`reference` = id del mensaje).

### Tope del plan y rate limit

Antes de gastar en el modelo, `UsageGateStage` pregunta al panel (`/internal/usage/sales-gate`, caché de 60 s):
- hasta el 110 % del tope mensual de turnos, sigue respondiendo;
- después manda un mensaje fijo de derivación y avisa al supervisor (una vez cada 12 h por cliente);
- rate limit: 20 turnos por cliente cada 10 min y 300 por negocio por hora.

### Telegram

`POST /api/v1/webhooks/telegram` recibe todo lo del bot compartido. Con `TELEGRAM_WEBHOOK_SECRET` definido (staging lo tiene),
exige que el header `X-Telegram-Bot-Api-Secret-Token` coincida.
- **Supervisor**: lo vincula el enlace `{agent_id}_<hex>`; recibe los avisos de pago y de derivación del agente.
- **Personas del equipo**: las vincula el enlace `m_<hex>`.
- **Director**: los mensajes privados, los botones `dir_*` y las fotos de facturas se reenvían al panel
  (`/internal/director/{message,callback,photo}`), y el agente manda la respuesta.

## Configuración del agente (`agent_instance.configuration`)

JSONB que se arma desde el panel. Lo que lee el agente (sin `checkout_requirements`, pide nombre, dirección y NIT):

```json
{
  "agent_info": {"name": "Vendedor Demo"},
  "personality": {
    "tone": "friendly",
    "formality_level": "casual",
    "emoji_usage": "moderate",
    "response_length": "concise",
    "brand_voice": "Tienda familiar con más de 20 años…",
    "custom_phrases": {"greeting": "¡Hola! Soy …", "farewell": "¡Gracias por tu tiempo!"}
  },
  "sales_process": {"QR_payment": true, "physical_payment": true, "QR_code": "<clave en R2 de la imagen del QR>"},
  "checkout_requirements": ["Nombre completo", "Dirección de entrega", "NIT"],
  "integrations": {"telegram": {"enabled": true, "supervisor_chat_id": "<chat del supervisor>"}},
  "response_settings": {},
  "conversation_settings": {}
}
```

Los datos del negocio (`tenant_info`, `operations_info`, `product_info`, `business_hours`) salen de `configuration_tenant`
y se combinan con esto en el contexto.

## Variables de entorno

Las de `.env.example` y `app/core/config.py`:

| Grupo | Variables |
|---|---|
| Base y caché | `DATABASE_URL` (asyncpg), `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_POOL_TIMEOUT`, `REDIS_URL`, `REDIS_TTL` |
| WhatsApp | `WHATSAPP_PROVIDER_DEFAULT` (`evolution`), `EVOLUTION_API_URL`, `EVOLUTION_API_KEY`, `EVOLUTION_WEBHOOK_SECRET`, `EVOLUTION_SEND_TEXT_PATH`, `EVOLUTION_SEND_MEDIA_PATH`; Twilio: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` |
| IA | `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_SMALL_MODEL`, `PROMPT_CACHE_ENABLED`, `LLM_PRICES`, `OPENAI_TEMPERATURE`, `OPENAI_MAX_TOKENS`, `USE_OPENROUTER`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` |
| Panel | `BACKEND_API_URL` (en docker, `http://yeison_backend:8070/api/v1`), `BACKEND_API_KEY` (= `INTERNAL_API_KEY` del panel), `BACKEND_API_TIMEOUT` |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` |
| Otros | `ENCRYPTION_KEY` (credenciales de Twilio por subcuenta), R2 (`r2_storage.py`) |

Nunca commitees un `.env` real.

## Correr en local

```bash
pip install -r requirements.txt pytest==8.3.4 pytest-asyncio==0.24.0
```

(Con el PR #20 queda `requirements-dev.txt`, que trae también ruff.)

```bash
python main.py
```

Necesita Redis y la base con el esquema de `yeison_migrations`. Para probar con WhatsApp de verdad, Evolution tiene que
poder llegar a tu webhook (en la práctica se prueba en staging).

## Modelos y contrato de esquema

`app/models/` mapea solo las tablas que usa el agente. Si cambia el esquema, primero va la migración en `yeison_migrations`,
después el modelo de aquí. El CI corre el contrato de esquema (`schema_contract.toml`) y falla si un modelo mapea algo que
no existe, con otro tipo, o que rompería un insert.

## Tests, CI y deploy

- `tests/`: unos 30 tests (webhook de Evolution, modelos por paso, `llm_usage`, registro de ventas, Director y enlaces de Telegram, tope
  del plan). Los que usan la base necesitan `TEST_DATABASE_URL` con el esquema al día.

  ```bash
  TEST_DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:55432/yeison_test python -m pytest -q tests
  ```

- `.github/workflows/ci.yml` (llega con el PR #20, que espera el secreto `MIGRATIONS_READ_KEY`), en cada PR a `staging` o
  `main` y en cada push a `staging`:
  - Postgres 16;
  - baja `yeison_migrations` y corre `alembic upgrade head`;
  - después `ruff`, el contrato de esquema y `pytest`.
  - Se mergea solo con el check `ci` en verde.
- `.github/workflows/deploy.yml`, con push a `staging` o `main`: SSH a la VPS, `git pull`, recrea `yeison_<env>_sales_agent` y
  espera `/health`.
