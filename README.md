# yeison_sales_agent
# WhatsApp AI Sales Agent - Architecture Documentation

## Project Overview

This is a WhatsApp AI Sales Agent that processes incoming WhatsApp messages through Twilio webhooks and executes a multi-step workflow to handle sales conversations.

**Key Principle**: This service focuses on **workflow execution only**. All CRUD operations for Tenant, Agent, Inventory, and Lead are handled by a separate main backend system.

## Architecture

### High-Level Flow

```
WhatsApp → Twilio → Webhook → Batch Queue (Redis) → Pipeline → WhatsApp Response
                                  ↓                      ↓
                              Wait 3s for              Database (read)
                              more messages            Redis (cache)
                                                       Main Backend API (mutations)
```

### Message Batching

The system implements intelligent message batching to handle rapid-fire messages from users:

- **Wait Window**: When a message arrives, the system waits 3 seconds to collect additional messages
- **Smart Processing**: Multiple messages from the same user are processed together as a single context
- **Early Firing**: If 5+ messages arrive before the 3-second window, processing happens immediately
- **Context Preservation**: All messages are saved individually in history, but AI generates one contextual response

**Example**: User sends "Hola" followed by "Quiero comprar zapatos" within 1 second → AI sees both messages as context and generates one relevant response about shoe products.

**Configuration**:
- `BATCH_ENABLED=true` - Enable/disable message batching
- `BATCH_WINDOW_SECONDS=3` - Wait time to collect messages
- `BATCH_MAX_MESSAGES=5` - Max messages before firing early

### Pipeline Stages

The agent processes each message through 6 sequential stages:

1. **Validation** - Validates message format and content
2. **Identification** - Identifies tenant, agent instance, and conversation
3. **Classification** - Classifies user intent using AI/LLM
4. **Context Building** - Builds conversation context with history and data
5. **Action Execution** - Executes appropriate actions based on intent
6. **Response Generation** - Generates and sends response via WhatsApp

## Project Structure

```
yeison_sales_agent/
├── app/
│   ├── api/
│   │   └── v1/
│   │       └── webhooks.py          # Twilio webhook endpoint
│   ├── core/
│   │   ├── config.py                # Configuration settings
│   │   ├── database.py              # Database session management
│   │   └── redis_client.py          # Redis caching + batch queue
│   ├── integrations/
│   │   └── whatsapp/
│   │       ├── client.py            # WhatsApp message sender
│   │       └── validator.py         # Twilio signature validation
│   ├── models/
│   │   ├── tenant.py                # Tenant model (read-only)
│   │   ├── agent_instance.py        # AgentInstance model
│   │   ├── inventory.py             # InventoryTenant model
│   │   ├── lead.py                  # Lead model (read-only)
│   │   └── conversation.py          # SalesConversation model
│   ├── schemas/
│   │   ├── webhook.py               # Twilio webhook schemas
│   │   ├── message.py               # Message schemas
│   │   └── pipeline.py              # Pipeline context schemas
│   └── services/
│       ├── batch_manager.py         # Message batching service
│       └── pipeline/
│           ├── base.py              # Base pipeline stage interface
│           ├── runner.py            # Pipeline orchestrator
│           └── stages/
│               ├── validator.py
│               ├── identifier.py
│               ├── classifier.py
│               ├── context_builder.py
│               ├── action_executor.py
│               └── response_generator.py
├── main.py                          # FastAPI application entry point
├── requirements.txt                 # Python dependencies
├── Dockerfile                       # Docker configuration
└── .env.example                     # Environment variables template
```

## Database Schema

This service reads from an existing PostgreSQL database with the following tables:

- **tenant** - Customer/organization information
- **agent_instance** - AI agent configurations (phone_number, configuration JSON)
- **inventory_tenant** - Product catalog per tenant
- **lead** - Sales leads/prospects
- **sales_conversation** - Conversation state and message history (JSONB)

### Data Access Pattern

- **READ**: Direct database queries for tenant, agent, inventory, lead data
- **WRITE**: Only to `sales_conversation` table for conversation state
- **MUTATIONS**: Via REST API calls to main backend (creating/updating leads, analytics)

## Key Components

### 1. Webhook Endpoint (`app/api/v1/webhooks.py`)

- Receives POST requests from Twilio
- Validates Twilio signature for security
- Enqueues messages to Redis batch queue (if batching enabled)
- Returns 200 OK immediately (Twilio requires <15s response)

### 2. Batch Manager (`app/services/batch_manager.py`)

- Manages message queuing and batching logic
- Implements 3-second wait window with asyncio timers
- Deduplicates messages using Twilio `message_sid`
- Fires early if max batch size reached (5 messages)
- Processes batches through pipeline with combined context

### 3. Pipeline Runner (`app/services/pipeline/runner.py`)

- Orchestrates all 6 pipeline stages
- Handles errors and logging
- Tracks processing time and metrics
- Supports both single and batched message processing

### 4. Pipeline Stages

#### Validation Stage
- Validates message format
- Checks required fields
- Basic spam detection (TODO)

#### Identification Stage
- Looks up agent instance by phone number
- Loads tenant configuration
- Finds or creates conversation
- Uses Redis caching for performance

#### Classification Stage
- Classifies user intent using AI/LLM
- Currently uses simple keyword matching (placeholder)
- **TODO**: Implement OpenAI/Anthropic integration

#### Context Builder Stage
- Loads conversation history
- Queries relevant inventory items
- Loads lead information if exists
- Prepares data for AI response generation
- **Batching**: Combines multiple message bodies with newlines for unified context

#### Action Executor Stage
- Routes to appropriate action based on intent
- Executes business logic (check inventory, qualify lead, etc.)
- **TODO**: Integrate with main backend API for mutations

#### Response Generator Stage
- Generates natural language response
- Currently uses template-based responses (placeholder)
- **TODO**: Implement LLM-based response generation
- Sends message via Twilio
- Saves messages to conversation history
- **Batching**: Saves all user messages individually, then one assistant response
- Invalidates conversation cache after saving

### 4. Caching Layer (`app/core/redis_client.py`)

- Caches tenant configurations
- Caches agent instance data
- Cache keys: `tenant:{id}`, `agent:{id}`, `agent:phone:{number}`
- Default TTL: 1 hour
- **Message Batching**:
  - Redis list operations for message queues
  - Distributed locks for batch timer coordination
  - Queue keys: `batch_queue:{agent_phone}:{user_phone}`
  - Lock keys: `batch_lock:{agent_phone}:{user_phone}`

## Intent Types

The system recognizes these user intents:

- `greeting` - Initial greeting
- `product_inquiry` - Asking about products
- `pricing_question` - Asking about prices
- `availability_check` - Checking product availability
- `purchase_intent` - Intent to purchase
- `objection` - Sales objection
- `complaint` - Customer complaint
- `support_request` - Support request
- `closing` - Ending conversation
- `general_question` - General inquiry
- `unknown` - Unclassified

## Configuration

Environment variables (see `.env.example`):

```bash
# Database
DATABASE_URL=postgresql://user:pass@host:5432/db

# Redis
REDIS_URL=redis://localhost:6379/0

# Message Batching
BATCH_ENABLED=true
BATCH_WINDOW_SECONDS=3
BATCH_MAX_MESSAGES=5

# Twilio
TWILIO_ACCOUNT_SID=your_sid
TWILIO_AUTH_TOKEN=your_token
TWILIO_PHONE_NUMBER=+14155238886

# OpenAI
OPENAI_API_KEY=your_key
OPENAI_MODEL=gpt-4-turbo-preview

# Main Backend
BACKEND_API_URL=http://localhost:3000/api
BACKEND_API_KEY=your_api_key
```

## Next Steps (TODOs)

### High Priority

1. **Implement LLM Integration**
   - Add OpenAI client in classifier stage
   - Create prompt templates for intent classification
   - Implement LLM-based response generation (currently using templates)

2. **Main Backend API Client**
   - Create HTTP client for main backend
   - Implement lead creation/update endpoints
   - Add analytics event tracking

3. **Message Batching Enhancements**
   - Add monitoring/metrics for batch processing
   - Implement batch retry logic on failures
   - Add batch size analytics and optimization

4. **Alembic Migrations**
   - Set up Alembic for database migrations
   - Create initial migration from existing schema

### Medium Priority

5. **Advanced Intent Classification**
   - Fine-tune prompts for better accuracy
   - Add confidence thresholds
   - Implement fallback strategies

6. **Conversation State Machine**
   - Track conversation states (greeting → inquiry → qualification → closing)
   - Implement context-aware responses

7. **Error Handling & Monitoring**
   - Add Sentry for error tracking
   - Implement retry logic
   - Add Prometheus metrics

### Low Priority

8. **Testing**
   - Unit tests for pipeline stages
   - Integration tests for webhook
   - Mock Twilio responses
   - Test message batching scenarios

9. **Performance Optimization**
   - Implement connection pooling
   - Add rate limiting
   - Optimize database queries

## Running the Application

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your credentials

# Run the server
python main.py
```

### Docker

```bash
# Build image
docker build -t yeison-sales-agent .

# Run container
docker run -p 8000:8090 --env-file .env yeison-sales-agent
```

### Testing Webhook

```bash
# Use ngrok to expose local server
ngrok http 8000

# Configure Twilio webhook URL:
# https://your-ngrok-url.ngrok.io/api/v1/webhooks/twilio
```

## API Endpoints

- `GET /` - Welcome message
- `GET /health` - Health check
- `GET /api/v1/webhooks/twilio` - Webhook verification
- `POST /api/v1/webhooks/twilio` - Receive WhatsApp messages

## Security

- Twilio signature validation on all webhook requests
- Database connection pooling with pre-ping
- Redis connection with authentication
- Non-root Docker user
- Environment-based secrets

## Logging

- Structured logging with structlog
- Correlation IDs for tracing
- Logs include: stage, message_sid, intent, action, processing_time

## License

[Your License Here]
