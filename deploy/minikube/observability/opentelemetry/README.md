# Distributed tracing and Langfuse

## Architecture and investigation (2026-10-22)

There is no Compose deployment in this repository. The active service manifests
are under `services/`; the collector and Jaeger run in the `observability` namespace
from `deploy/minikube/observability/otel-collector/` and `deploy/minikube/observability/jaeger/`.
No AGENTS.md was found in the repository or workspace root during this review.

General application spans use OTLP/HTTP → otel-collector → Jaeger. Prometheus
collects metrics and Grafana displays them; neither replaces the trace backend.
Python/Node already had service-local OTLP JSON instrumentation. This change reuses
it, including its parent sampling decision and bounded transport. Shipping retains
Spring Boot/Micrometer automatic HTTP and Rabbit listener instrumentation. FastMCP's
native SDK spans are now exported, rather than creating a second tool span. Moks
uses OpenTelemetry SDK generation spans bridged into its existing exporter.

Confirmed in code:

* Python's route categorization produced `GET other`, including internal endpoints.
  Node/Python clients used `HTTP GET` without a known target template.
* Moks exported **all** service spans to Langfuse, including ordinary HTTP work.
* The widget invented an outgoing parent span ID without exporting that span.
  Kafka forwarded an older event context without consumer/producer observations;
  `tracestate` was not consistently propagated.
* There was no evaluation-score submission implementation. Traces alone do not
  create evaluation scores.
* Generation counts were emitted, but no explicit costs were calculated. The
  configured inference-profile ID was the model matching key.

The reported 284 traces/525 observations are **not evidence of duplicates**:
a workflow normally contains multiple observations. Those counts and the exact
cause of the displayed $0.00 were not inspected live. Rounding, a missing model
price definition, incomplete usage and a genuine zero-priced usage category cannot
be distinguished from a screenshot alone. No live Langfuse/AWS access was used.

## Service and naming inventory

HTTP names use `METHOD /route/template` from handler vocabulary. Unknown routes
use only the method, with no raw path/query. Mounted `/roboshop/api/...` inputs
retain the prefix when present; ingress-stripped requests use backend route names.
`http.route` is a template, never a search phrase, SKU, address or customer ID.
Client spans use known URL templates where available, otherwise just the method.

| Service / component | Framework / interface | Representative spans or correlation |
| --- | --- | --- |
| ai-gateway | FastAPI/AnyIO, synchronous domain workers | `POST /api/payments/orders`, `GET /api/products/{product_id}`, `cart.update`, `checkout.reserve`, `payment.capture`; Mongo command and PostgreSQL operation spans |
| gateway background workers | Threaded outbox publisher, purchase projection | `publish roboshop.orders`, `purchase_history.project`; durable event parent context, independent retry attempts |
| user-service | Python/Waitress, MongoDB, SES SMTP | `POST /internal/request`, `auth.login`, `auth.register`, Mongo command spans; credentials/addresses excluded |
| cart-service | Python/Waitress, Redis Lua CAS | `/internal/carts/{cart_id}`, `GET`, `SET`, `EVAL`, `EXPIRE` Redis commands; keys/scripts/values excluded |
| catalogue-service | Node HTTP, MongoDB command monitoring | `GET /api/catalogue/search/{query}`, `catalogue.search`, `catalogue.read`, `catalogue.recommend`, Mongo commands |
| ingestion | Integrated catalogue startup/CLI, not another service | `catalogue.ingest`; insert-only behavior unchanged |
| recommendations | Integrated catalogue ranking, not another service | `GET /api/recommendations`, `catalogue.recommend` |
| moks | Waitress, LangGraph/LangChain | `POST /internal/chat`, `rag.retrieve`, `shopping_agent.recommend`, `llm.route`, one `llm.generate` per attempted provider |
| Moks widget (formerly Finder) | Same Moks process, separate authenticated routes | `POST /chat`, `GET /history`; HTTP context forwarded to gateway, not all exported to Langfuse |
| RAG | In-process catalogue retrieval, no vector database/service | `rag.retrieve`; catalogue HTTP request is linked under originating request |
| MCP | FastMCP 4.1.3 HTTP/stdio | `POST /mcp`, native `mcp.call_tool`/`mcp.request`; native tool parent used by outgoing gateway client spans |
| payment-service | Node HTTP, Razorpay REST | `POST /api/payment/verify`, `payment.create_order`, `payment.verify`, `payment.verify_signature`; provider client spans, no signatures/bodies |
| shipping-service | Spring MVC, JDBC/Redis, Spring AMQP | `POST /api/shipping/quote`, `shipping.calculate`, automatic Rabbit consumer observation, `shipping.record_request` |
| currency-service | Go net/http, hourly ECB refresh | `GET /rates`, `currency.refresh_rates`, fixed ECB HTTP client span; bounded OTLP export |
| kafka-bridge | Python worker, pika + librdkafka | `process roboshop.kafka.orders`, `publish roboshop.orders.live.v1` or `.test.v1`; headers link consumer → producer |
| web | Stock NGINX serving the TypeScript storefront and proxying `/api` | W3C headers forwarded; safe access-log correlation (`"service":"web"`). **No native spans**: image has no OTel module; no browser RUM/SDK |
| mongodb, postgres, redis | Database infrastructure | Client spans above; not instrumented database-server processes |
| rabbitmq, kafka | Messaging infrastructure | Producer/consumer spans above; broker metrics/logs remain separate |
| otel-collector, jaeger | Trace transport/backend | Collector filtering/privacy/batching; no application generations |
| monitoring Grafana/Prometheus | External monitoring deployments | Metrics/dashboard infrastructure, no application trace export added |

SDK/dependency audit: Moks declares LangChain Core 1.6.12, LangGraph 1.2.16,
boto3 1.43.114; gateway FastAPI 0.156.3; PyMongo 4.19.0; Node MongoDB 7.7.0.
Moks and MCP now pin OpenTelemetry SDK 1.45.1. Shipping's existing Gradle lock pins
Spring Boot 4.1.1, Micrometer 1.17.1/tracing 1.7.1 and OTel 1.62.0. Collector stays
at 0.162.0. No automatic framework/provider instrumentation was added on top of
existing spans. A Python test environment was aligned to the declared versions.

## Context, resources, noise and privacy

W3C `traceparent` and bounded `tracestate` travel through HTTP and supported
RabbitMQ/Kafka headers. Context variables/AsyncLocalStorage preserve async work;
existing AnyIO/thread-pool context copying is retained. Each outbox event is processed
individually, so a parent relationship is used, not a misleading batch parent.
Old events lacking context create new roots. Retries never replay business operations
for tracing. Repeated message deliveries get distinct spans and retain event
idempotency behavior. Purchase projection uses the saved paid-event context.

Resources: `service.name`, `service.namespace` (`OTEL_SERVICE_NAMESPACE`, default
`roboshop-ai`), `service.version` (`OTEL_SERVICE_VERSION`, default `unknown`),
`service.instance.id` (`POD_UID` or hostname), and `deployment.environment.name`
(`DEPLOYMENT_ENVIRONMENT`). Set version to the deployed release/commit in deployment
configuration; a mutable `latest` image does not prove its source revision.
No second probabilistic sampler is installed. New roots retain existing full-sampling
behavior; sampled/unsampled parent flags are respected.

Successful health/readiness/metrics and widget-static requests are filtered by
route attributes. Ordinary catalogue GETs are retained. Failed probes remain visible.
The collector also filters successful Spring health spans and removes exception
events, while retaining error status. Sources and the embedded Minikube collector
configuration are checked for consistency. Child errors of suppressed probes can
appear without their successful parent, intentionally preserving error visibility.

Structured application logs include `trace_id`/`span_id` where context exists.
Login identities are no longer logged as email addresses: set a backend-only
`TELEMETRY_ID_KEY` for HMAC pseudonyms, or leave it absent to omit identities.
No user/session identifiers are sent to Langfuse by default. Provider credentials,
headers, payment fields, tool arguments/results and SQL statements are not captured.

`LANGFUSE_CAPTURE_CONTENT=false` is the default. The only opt-in supported is
`synthetic-only`, and it additionally requires an explicitly marked evaluation
context (`traffic=evaluation`). It cannot enable capture of live customer chats.
Known credential/email/number patterns are redacted even for synthetic content;
free-text redaction is not a general PII detector. LangSmith ambient tracing stays
explicitly disabled. Langfuse keys remain backend Secret references.

Export is best effort: bounded Python queues (32 entries, two daemon workers),
bounded Node in-flight exports, and eight Go exports; network deadlines are two
seconds. Python/Go shutdown draining is bounded to two seconds, not a delivery
guarantee. Disabled/unavailable/closed exporters do not fail commerce requests.
Abrupt process termination can lose observations. No unlimited export retry queue.

## Langfuse generation data and costs

Only marked AI observations (`agent`, `retriever`, `chain`, `generation`) go to
Langfuse; the same trace IDs correlate with Jaeger. General HTTP, DB and broker spans
stay in Jaeger. Each actual provider attempt creates exactly one SDK generation,
including failed attempts; routing/graph spans are not generations. Provider SDK
retries, failover eligibility, budgets, authorization and tool behavior are unchanged.

Generation attributes include requested model/profile, provider, outcome, duration,
failover reason, available input/output usage and Bedrock cache-read/cache-write
usage. Missing values remain absent; no fabricated zero counts, costs or totals.
Bedrock Converse does not supply a resolved backend model ID here: the requested
profile is retained and tagged `requested_identifier`, not represented as a resolved
model. Ollama's returned model is retained when supplied. These adapters are
non-streaming; there is no streaming completion usage to collect. Reasoning-token
usage is not inferred when the adapters do not return it.

For `global.amazon.nova-2-lite-v1:0`, inspect a new **generation** in Langfuse:

1. Confirm model/profile spelling, input/output usage and observation type.
2. Inspect the numeric cost detail, not only the rounded dashboard total.
3. In Project Settings → Models, check for a matching definition. If absent, create
   a reviewed custom definition matching `^global\.amazon\.nova-2-lite-v1:0$`.
4. Enter the applicable, current AWS model/inference-tier/region pricing per usage
   unit. Match `input`, `output`, `cache_read_input_tokens` and
   `cache_write_input_tokens` only where applicable. Do not guess a price or silently
   alias an inference profile to another model. No prices are hardcoded here.
5. Inspect newly ingested generations; don't assume existing observations are
   automatically repriced. Costs are **estimates**; AWS billing is authoritative.
   Ollama's lack of a per-token API charge does not mean free infrastructure.

Sources consulted: [Langfuse OTLP mapping](https://langfuse.com/integrations/native/opentelemetry),
[model definitions](https://langfuse.com/docs/observability/features/token-and-cost-tracking),
[Bedrock usage fields](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_TokenUsage.html),
[HTTP semantic conventions](https://opentelemetry.io/docs/specs/semconv/http/http-spans/),
[FastMCP native telemetry](https://gofastmcp.com/servers/telemetry),
[OTel Python SDK API](https://opentelemetry-python.readthedocs.io/en/latest/sdk/trace.html),
[Spring AMQP observations](https://docs.spring.io/spring-amqp/reference/amqp/receiving-messages/micrometer-observation.html).

## Explicit evaluations

`evaluations/agents/catalogue-v1/dataset.json` is synthetic and versioned, independent
of merchant inventory. `evaluate.py` checks recorded product IDs, selection,
stock, budget/currency, price and structured specification claims. It never invokes
models. Free-text factual accuracy is **not** proven by structured checks; human
review or a separately approved judge is needed. No LLM judge is enabled.

Each result row must include `datasetVersion: catalogue-v1`, `traffic: evaluation`,
`caseId`, actual `traceId` and `observationId`, and `products`. Optional `claims` are
`{sku, field, value}` assertions. Missing required evidence fails, not a perfect
placeholder score. Supply IDs from the actual evaluated generation, never fabricated
IDs or another user execution. No automatic production scoring or fixture import.

```bash
python3 evaluations/agents/evaluate.py --results /path/to/recorded-evaluation-results.json
# Explicit upload only, using backend environment credentials:
python3 evaluations/agents/evaluate.py --results /path/to/recorded-evaluation-results.json --publish
```

Scores use versioned names and deterministic IDs linked to **both** trace and
observation, making resubmission idempotent. Upload uses the supported
[Scores API](https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk).
Tests include deliberately incorrect output; mock test results are not published.

## Rollout and connected-trace checklist

No live deployment or project settings were changed. Rebuild the affected application
images (Python services, Node services, Moks/MCP, currency, shipping and proxies).
Moks/MCP need their new SDK dependency; do not copy only source files into old images.
Apply the updated consolidated ConfigMap and service manifests through the normal
workflow, including the collector restart needed to load its updated configuration.
No Secret rotation, business-data migration, provider calls or payment actions are
required. Existing Langfuse URL/project keys still work. No Compose/Helm application
chart was introduced; archived reference-only AWS collector examples are unchanged.

After a separately authorized deployment:

- Perform a catalogue search; use response `X-Request-ID` to locate the gateway
  trace in Jaeger. Check catalogue child HTTP and Mongo spans share its trace ID.
- Add/remove a cart item; inspect gateway → cart-service → Redis command spans.
- In sandbox only, inspect checkout reservation → payment-service/provider and paid
  outbox → Rabbit consumer/Kafka producer, plus purchase projection. Verify parents
  and immutable payment mode without exposing payment data.
- Ask Moks a question; find `POST /internal/chat`, retrieval, chain, routing and
  generation children. Compare the trace ID with Langfuse.
- For mocked/approved failover, expect one failed primary generation and one fallback
  generation under the same routing span, with different observation IDs.
- Check health noise is absent, failed probes remain, and unrelated catalogue GETs
  have not been filtered. Check no request text/IDs appear in names.
- Inspect generation usage/model definitions and an explicitly uploaded evaluation
  score. Do not infer success from aggregate dashboard counts alone.

Local commands and verification results: [verification.md](verification.md).
