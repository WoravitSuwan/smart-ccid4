import pytest

from accounts.tests.factories import UserFactory


@pytest.fixture
def make_user(db):
    def _make(**kw):
        return UserFactory(**kw)

    return _make


@pytest.fixture
def investigator(make_user):
    return make_user(role="investigator", username="inv")


@pytest.fixture
def client_inv(client, investigator):
    client.force_login(investigator)
    return client
