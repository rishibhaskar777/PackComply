"""Existing inspection tests exercise authenticated behaviour with a test principal."""
import os
from uuid import UUID

# Tests use fakes; never load real Paddle models from the app lifespan.
os.environ.setdefault("OCR_WARMUP_ON_STARTUP", "false")

import pytest

from backend.app.api.auth import get_current_user, login_limiter
from backend.app.main import app
from backend.app.models import User

TEST_USER_ID = UUID("00000000-0000-0000-0000-000000000001")

@pytest.fixture(autouse=True)
def authenticated_inspection_tests():
    login_limiter.reset()
    app.dependency_overrides[get_current_user] = lambda: User(id=TEST_USER_ID, username="test", email="test@example.invalid", full_name="Test", password_hash="unused", is_active=True)
    yield
    app.dependency_overrides.clear()
