import pytest

from scamshield.config import Settings
from scamshield.llm import FakeLLMClient
from scamshield.memory import MemoryStore
from scamshield.pipeline import Pipeline
from scamshield.runtime import load_profile
from scamshield.search import FakeSearchClient


@pytest.fixture
def settings():
    return Settings.from_env({"SCAMSHIELD_OFFLINE": "1"})


@pytest.fixture
def profile(settings):
    return load_profile(settings.seed_dir)


@pytest.fixture
def store(profile):
    s = MemoryStore(":memory:")
    s.save_profile(profile)
    return s


@pytest.fixture
def search(settings):
    return FakeSearchClient.from_file(settings.seed_dir / "search_fixtures.json")


@pytest.fixture
def pipeline(settings, store, search):
    return Pipeline(settings, FakeLLMClient(), search, store)
