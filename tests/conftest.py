"""Shared test fixtures.

Every test runs offline and spends no tokens: the agent is the rule-based fake
model, and no test can see an OpenAI key.
"""

import shutil
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from temporalio.testing import WorkflowEnvironment


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the fake model and hide any key a developer exported for a live demo."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("USE_FAKE_MODEL", "1")


@pytest_asyncio.fixture(scope="session")
async def env() -> AsyncIterator[WorkflowEnvironment]:
    """Start one throwaway local Temporal dev server for the whole test session."""
    # Reuses the `temporal` CLI if it is on PATH; otherwise the SDK downloads a
    # dev server the first time. Either way it uses a free port, not 7233.
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=shutil.which("temporal"),
    ) as environment:
        yield environment
