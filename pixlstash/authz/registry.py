"""The route-policy registry: the single authorization declaration table.

``ROUTE_POLICIES`` is the one place every mounted HTTP route declares its access
requirement, keyed by ``(method, effective_path_template)`` — the *prefixed*
path as enumerated by :func:`pixlstash.route_inventory.iter_api_route_contexts`
(e.g. ``("GET", "/api/v1/pictures/{picture_id}/thumbnail")``). It IS the coverage
matrix: reviewable in one screen, diffable, greppable. See the backend refactor
plan §3.2 and ``docs/backend_architecture.md`` §16.2.

**Empty by design in Phase 1 Step 1.** The declaration back-fill for all mounted
routes is Step 2 (a separate PR that carries the adversarial security review). In
Step 1 the registry is empty, so the gate treats every route as undeclared and —
in report-only mode — logs the full backlog rather than denying anything. The CI
guardrail's audit allowlist (``tests/test_architecture_guardrails.py``) therefore
still holds the full current route set; it burns down as this table fills.
"""

from __future__ import annotations

from pixlstash.authz.policy import RoutePolicy

# Step 2 back-fills this table with one entry per mounted (method, path). Until
# then it is intentionally empty: no route is declared, so the deny-by-default
# gate would deny every route if it were enforcing. It is NOT enforcing in Step 1
# (see AUTHZ_GATE_ENFORCING in pixlstash/authz/gate.py) — it reports only.
ROUTE_POLICIES: dict[tuple[str, str], RoutePolicy] = {}

__all__ = ["ROUTE_POLICIES"]
