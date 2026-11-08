# Focused tracing review fixes

HTTP dispatch uses service-local bounded vocabularies because gateway FastAPI has
an intentional catch-all route. Its framework template would lose operation
names. Spring shipping keeps the matched framework path pattern. MCP's transport
has a bounded `/mcp` route; native tool observations retain their existing names.
Static admin routes include delegated handlers in `admin.py` and `llm.py`, not
only `server.py`. Mounted `/roboshop` paths and dynamic identifiers are normalized;
unknown paths remain method-only. Mounted health routes retain filtering.
`tests/test_tracing_review.py` scans production API/internal literals and checks
service-local copy parity; Node tests compare their vocabulary with Python.
Dynamic examples additionally guard parameter redaction. Nonliteral new routing
schemes still require a reviewed template and regression fixture.

Python and Node client spans now carry response metadata separately from the
application's sanitized error. A received 429 remains 429 on the client span even
when the receiving service returns 502. No response means no HTTP status attribute.
A retry clears the previous attempt's response status before attempting transport.
The enclosing logical client span records the last attempt, not one span per retry.
Retry eligibility, deadlines and write/payment retry policy are unchanged.
Internal/database spans no longer invent HTTP status attributes. Server spans
continue to record the actual application response.

The widget and Razorpay-specific transport wrappers use the same rule. Currency's
Go transport previously omitted the response attribute; it now records only a
received response. Shipping uses framework instrumentation and has no custom
upstream HTTP error translation requiring this correction.

All verification uses mocked transports/local servers. No models, payment APIs,
Langfuse ingestion or deployments are invoked. See the delivery summary for test
results; tests do not prove live collector or Cloud ingestion availability.
