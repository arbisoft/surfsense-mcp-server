"""Slow smoke test: build the production Docker image and prove its imports work.

Skipped by default (`@pytest.mark.slow`) so the regular `pytest` run stays
fast. Also skips automatically if Docker isn't available (no daemon, no
`docker` Python SDK, or the `from_env()` ping fails).

Invoke explicitly:

    pytest tests/test_smoke_image.py -m slow

CI runs this on `workflow_dispatch` only, not on every PR.
"""

from __future__ import annotations

import pathlib

import pytest

# Skip the entire module if the docker SDK isn't installed (it's an optional
# dev dep; surfsense's pyproject doesn't pin it).
docker = pytest.importorskip("docker")

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

pytestmark = [pytest.mark.slow]


@pytest.fixture(scope="module")
def docker_client():
    """Skip the module if no daemon is reachable."""
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:
        pytest.skip(f"Docker not available: {exc}")
    return client


@pytest.fixture(scope="module")
def built_image(docker_client):
    """Build the prod image once per session."""
    image, _logs = docker_client.images.build(
        path=str(REPO_ROOT),
        dockerfile="Dockerfile",
        tag="surfsense-mcp:pytest-smoke",
        rm=True,
        forcerm=True,
    )
    yield image
    try:
        docker_client.images.remove(image.id, force=True)
    except Exception:
        pass


def test_image_imports_both_packages(docker_client, built_image):
    """The runtime image must have both surfsense_mcp and moneta_mcp_auth importable.

    Catches Dockerfile regressions where the wheel build skips the lib dep
    or where pip silently fails to install moneta-mcp-auth from PyPI.
    """
    output = docker_client.containers.run(
        image=built_image.id,
        entrypoint=["python", "-c"],
        command=[
            "import surfsense_mcp, moneta_mcp_auth; "
            "print('OK', moneta_mcp_auth.__version__)"
        ],
        remove=True,
    )
    decoded = output.decode("utf-8")
    assert decoded.startswith("OK "), f"unexpected output: {decoded!r}"


def test_image_http_mode_env_guard_fires(docker_client, built_image):
    """Without HTTP env vars, http mode must raise the lib's clear ValueError.

    Validates that the moneta-mcp-auth env-guard is wired up via
    require_http_env_vars() inside the prod image, not bypassed somewhere.
    """
    try:
        docker_client.containers.run(
            image=built_image.id,
            command=["http"],
            environment={"SURFSENSE_BASE_URL": "http://nope:8000"},
            remove=True,
        )
    except docker.errors.ContainerError as exc:
        stderr = exc.stderr.decode("utf-8") if exc.stderr else ""
        assert "missing required env vars" in stderr, f"unexpected error: {stderr}"
    else:
        pytest.fail("Container exited 0 — expected non-zero exit when http env is missing")
