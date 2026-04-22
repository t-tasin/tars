"""Verify Prometheus /metrics endpoint is wired via fastapi-instrumentator."""

from __future__ import annotations


def test_metrics_endpoint_mounted() -> None:
    """/metrics must be registered on the FastAPI app.

    We inspect app.routes rather than spinning up a TestClient so the
    lifespan (which needs Postgres/Redis) does not run.
    """
    from src.main import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/metrics" in paths


def test_instrumentator_attached_to_app() -> None:
    """Sanity check: the instrumentator hook placed a route whose
    endpoint function comes from prometheus-fastapi-instrumentator."""
    from src.main import app

    metrics_route = next(
        (r for r in app.routes if getattr(r, "path", None) == "/metrics"),
        None,
    )
    assert metrics_route is not None
    endpoint_module = getattr(metrics_route.endpoint, "__module__", "")
    assert "prometheus_fastapi_instrumentator" in endpoint_module
