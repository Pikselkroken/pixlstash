"""Centralised, deny-by-default authorization for the HTTP API.

Phase 1 of the backend authorization refactor (see ``docs/backend_architecture.md``
§16.2 and the backend refactor plan §3). This package holds the three pieces of
the target model:

- :mod:`pixlstash.authz.policy` - the closed :class:`~pixlstash.authz.policy.AccessPolicy`
  vocabulary and the :class:`~pixlstash.authz.policy.RoutePolicy` declaration record.
- :mod:`pixlstash.authz.registry` - ``ROUTE_POLICIES``, the single declaration
  table (the coverage matrix). Empty in Step 1; back-filled in Step 2.
- :mod:`pixlstash.authz.gate` - the router-level dependency + startup enumeration.

Step 1 ships the gate in REPORT-ONLY mode: it denies nothing at runtime and only
observes the undeclared-route backlog. The fail-closed machinery exists behind the
``AUTHZ_GATE_ENFORCING`` code constant and is proven by the decoy-route guardrail
test; later steps flip it on.
"""

from pixlstash.authz.gate import AUTHZ_GATE_ENFORCING, AuthzGate
from pixlstash.authz.policy import AccessPolicy, RoutePolicy
from pixlstash.authz.registry import ROUTE_POLICIES

__all__ = [
    "AUTHZ_GATE_ENFORCING",
    "AuthzGate",
    "AccessPolicy",
    "RoutePolicy",
    "ROUTE_POLICIES",
]
