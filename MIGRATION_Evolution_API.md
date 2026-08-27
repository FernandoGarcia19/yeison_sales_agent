# Evolution API migration notes

This project is migrating from Twilio as the primary WhatsApp provider to a single shared Evolution API host.
The new model is:

- one Evolution API deployment
- one shared set of Evolution credentials in `.env`
- one Evolution instance/connection per agent stored in the database
- Twilio kept only as deprecated rollback code

## Old vs new model

### Before

- Twilio received inbound webhooks.
- Twilio credentials were resolved per agent.
- Twilio signed webhook validation was part of the hot path.
- Twilio-authenticated media downloads were used for payment proof files.
- Outbound WhatsApp sends were performed through Twilio clients.

### After

- Evolution receives inbound webhooks.
- A single shared Evolution host is configured in `.env`.
- Each agent stores its own Evolution `instance_name` in `whatsapp_connection`.
- Webhook payloads are normalized into an internal provider-neutral message model.
- Outbound sends use the shared Evolution host plus the agent's instance name.
- Media downloads use the same Evolution host settings.
- Twilio remains in the codebase only as deprecated fallback support.

## Storage model

### Shared configuration in `.env`

- `EVOLUTION_API_URL`
- `EVOLUTION_API_KEY`
- `EVOLUTION_WEBHOOK_SECRET`
- `EVOLUTION_SEND_TEXT_PATH`
- `EVOLUTION_SEND_MEDIA_PATH`

These values are shared by all agents and are not stored per agent.

### Per-agent storage in `whatsapp_connection`

- `provider` = `evolution`
- `evolution_instance_name`
- `whatsapp_phone_number`

Legacy Twilio columns still exist for rollback purposes, but the Evolution runtime path does not depend on them.

## Runtime flow

### Inbound messages

1. Evolution posts the webhook to `/api/v1/webhooks/evolution`.
2. The payload is normalized into the internal WhatsApp webhook request model.
3. The agent is resolved from the recipient phone / instance mapping.
4. The message is deduplicated and queued or processed immediately.
5. The pipeline runs exactly as before, but it now receives a provider-neutral message envelope.

### Outbound messages

1. The sender agent is resolved from `whatsapp_connection`.
2. The agent's `evolution_instance_name` is loaded.
3. The shared Evolution host URL and API key are taken from `.env`.
4. The message is sent through the Evolution API.
5. Twilio send helpers are only used when explicitly invoking deprecated compatibility code.

### Payment-proof media

Payment-proof downloads were previously Twilio-authenticated. They now use the shared Evolution host settings and the current agent mapping instead.

## Database migration

The database changes are manual SQL, not Alembic-driven, because this repository already follows that pattern.

Minimum schema update:

- add `provider` to `whatsapp_connection`
- add `evolution_instance_name` to `whatsapp_connection`
- keep the existing Twilio columns for rollback only

If you previously added any per-agent Evolution host fields, remove them. The host is now shared globally.

## Cutover steps

1. Set the Evolution environment variables in `.env`.
2. Fill `whatsapp_connection.evolution_instance_name` for each agent.
3. Update the Evolution webhooks to point to `/api/v1/webhooks/evolution`.
4. Verify outbound messages are being sent through the shared Evolution host.
5. Verify media uploads/downloads work for payment proof flows.
6. Keep the Twilio webhook and sender code only if you want rollback coverage.

## Rollback posture

Twilio has not been deleted. It is deprecated in the codebase and can still be used as a fallback if needed.
That said, the production path should be treated as Evolution-only after migration.

## Operational notes

- `EVOLUTION_INSTANCE_NAME` is not a global fallback. Instance names are per agent.
- The shared Evolution API key is not encrypted per agent because it is no longer stored per agent.
- If the architecture ever changes to multiple Evolution hosts, provider routing can be reintroduced later.