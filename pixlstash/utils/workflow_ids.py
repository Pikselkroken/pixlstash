"""What a workflow id looks like, in one place and with no imports.

``auto:<core hash>`` names an automatic workflow (the topologies sharing that
core hash); ``manual:<uuid4 hex>`` names a manual one, a hub row holding its
own document (``workflow_document``). Stdlib only, so the MCP server, which is
a light separate process, can check an id without importing the hub.

The pattern is also a JSON-schema one, where ``$`` is the end; Python checks
it with ``fullmatch``, since there ``$`` also matches before a trailing newline.
"""

AUTO_PREFIX = "auto:"
MANUAL_PREFIX = "manual:"

WORKFLOW_ID_PATTERN = r"^(?:auto:[0-9a-f]{64}|manual:[0-9a-f]{32})$"
