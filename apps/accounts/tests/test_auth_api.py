"""Positive and negative API tests for Feature 002 authentication."""

from __future__ import annotations

from datetime import timedelta
from typing import cast
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.core import mail
from django.http import HttpRequest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import PasswordResetToken
from apps.accounts.views import CurrentUserView

AUTH_URL = "/api/v1/auth/"
STRONG_PASSWORD = "Violet!River_Orbit-Glass_9274"
RESET_PASSWORD = "New!Amber_Constellation-Paper_6842"


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def account_user(db):
    """Create a user using whichever user model Django is configured to use."""
    user_model = cast(type[User], get_user_model())
    return user_model.objects.create_user(
        username="alice",
        email="alice@example.com",
        password=STRONG_PASSWORD,
    )


def authenticate(client: APIClient, user) -> RefreshToken:
    """Authenticate a client and return a refresh token for logout tests."""
    refresh = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return refresh


@pytest.mark.django_db
def test_register_happy_path_creates_user_with_hashed_password(api_client):
    response = api_client.post(
        f"{AUTH_URL}register/",
        {
            "username": "new-user",
            "email": "new@example.com",
            "password": STRONG_PASSWORD,
            "password_confirm": STRONG_PASSWORD,
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["success"] is True
    assert response.json()["data"] == {
        "username": "new-user",
        "email": "new@example.com",
    }
    created_user = cast(User, get_user_model().objects.get(username="new-user"))
    assert created_user.email == "new@example.com"
    assert created_user.check_password(STRONG_PASSWORD)
    assert created_user.password != STRONG_PASSWORD
    assert STRONG_PASSWORD not in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("payload", "error_field"),
    [
        (
            {
                "username": "new-user",
                "email": "new@example.com",
                "password": STRONG_PASSWORD,
                "password_confirm": "Different!Password_184",
            },
            "password_confirm",
        ),
        (
            {
                "username": "new-user",
                "email": "not-an-email",
                "password": STRONG_PASSWORD,
                "password_confirm": STRONG_PASSWORD,
            },
            "email",
        ),
        (
            {
                "username": "new-user",
                "email": "new@example.com",
                "password": "password",
                "password_confirm": "password",
            },
            "password",
        ),
    ],
)
def test_register_rejects_invalid_input_without_creating_user(
    api_client,
    payload,
    error_field,
):
    response = api_client.post(
        f"{AUTH_URL}register/",
        payload,
        format="json",
    )

    assert response.status_code == 400
    assert get_user_model().objects.count() == 0
    assert error_field in response.json()


@pytest.mark.django_db
def test_register_rejects_existing_username(api_client, account_user):
    response = api_client.post(
        f"{AUTH_URL}register/",
        {
            "username": account_user.username,
            "email": "another@example.com",
            "password": STRONG_PASSWORD,
            "password_confirm": STRONG_PASSWORD,
        },
        format="json",
    )

    assert response.status_code == 400
    assert get_user_model().objects.count() == 1


@pytest.mark.django_db
def test_register_rejects_existing_email(api_client, account_user):
    response = api_client.post(
        f"{AUTH_URL}register/",
        {
            "username": "another-user",
            "email": account_user.email,
            "password": STRONG_PASSWORD,
            "password_confirm": STRONG_PASSWORD,
        },
        format="json",
    )

    assert response.status_code == 400
    assert get_user_model().objects.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("identifier", ["alice", "alice@example.com"])
def test_login_happy_path_accepts_username_or_email(
    api_client,
    account_user,
    identifier,
):
    response = api_client.post(
        f"{AUTH_URL}login/",
        {"username": identifier, "password": STRONG_PASSWORD},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["data"]["access"]
    assert response.json()["data"]["refresh"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("identifier", "password"),
    [
        ("alice", "incorrect-password"),
        ("unknown@example.com", STRONG_PASSWORD),
    ],
)
def test_login_rejects_invalid_credentials(
    api_client,
    account_user,
    identifier,
    password,
):
    response = api_client.post(
        f"{AUTH_URL}login/",
        {"username": identifier, "password": password},
        format="json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_login_rejects_inactive_account(api_client, account_user):
    account_user.is_active = False
    account_user.save(update_fields=["is_active"])

    response = api_client.post(
        f"{AUTH_URL}login/",
        {
            "username": account_user.username,
            "password": STRONG_PASSWORD,
        },
        format="json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_token_refresh_happy_path_returns_new_access_token(api_client, account_user):
    refresh = RefreshToken.for_user(account_user)
    old_token_id = refresh["jti"]

    response = api_client.post(
        f"{AUTH_URL}token/refresh/",
        {"refresh": str(refresh)},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["access"]
    assert response.json()["refresh"]
    assert BlacklistedToken.objects.filter(token__jti=old_token_id).exists()

    replay_response = api_client.post(
        f"{AUTH_URL}token/refresh/",
        {"refresh": str(refresh)},
        format="json",
    )
    assert replay_response.status_code == 401


@pytest.mark.django_db
def test_token_refresh_rejects_invalid_token(api_client):
    response = api_client.post(
        f"{AUTH_URL}token/refresh/",
        {"refresh": "not-a-refresh-token"},
        format="json",
    )

    assert response.status_code == 401


@pytest.mark.django_db
def test_current_user_rejects_anonymous_request(api_client):
    response = api_client.get(f"{AUTH_URL}me/")

    assert response.status_code == 401


@pytest.mark.django_db
def test_current_user_get_is_not_supported(account_user):
    view = CurrentUserView()
    raw_request = APIRequestFactory().get(f"{AUTH_URL}me/")
    force_authenticate(raw_request, user=account_user)
    request = view.initialize_request(raw_request)
    view.request = cast(HttpRequest, request)

    assert view.get(request) is account_user


@pytest.mark.django_db
def test_logout_rejects_anonymous_request(api_client, account_user):
    refresh = RefreshToken.for_user(account_user)

    response = api_client.post(
        f"{AUTH_URL}logout/",
        {"refresh": str(refresh)},
        format="json",
    )

    assert response.status_code == 401


@pytest.mark.django_db
def test_logout_happy_path_blacklists_refresh_token(api_client, account_user):
    refresh = authenticate(api_client, account_user)
    token_id = refresh["jti"]

    response = api_client.post(
        f"{AUTH_URL}logout/",
        {"refresh": str(refresh)},
        format="json",
    )

    assert response.status_code == 205
    assert BlacklistedToken.objects.filter(token__jti=token_id).exists()


@pytest.mark.django_db
def test_logout_rejects_malformed_refresh_token(api_client, account_user):
    authenticate(api_client, account_user)

    response = api_client.post(
        f"{AUTH_URL}logout/",
        {"refresh": "not-a-refresh-token"},
        format="json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_password_reset_request_happy_path_sends_link_and_stores_hash(
    api_client,
    account_user,
):
    response = api_client.post(
        f"{AUTH_URL}password-reset/",
        {"email": account_user.email},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert len(mail.outbox) == 1

    reset_record = PasswordResetToken.objects.get(user=account_user)
    reset_url = mail.outbox[0].body.splitlines()[-1]
    raw_token = parse_qs(urlparse(reset_url).query)["token"][0]
    assert reset_record.token_hash == PasswordResetToken.hash_token(raw_token)
    assert reset_record.token_hash != raw_token


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_password_reset_unknown_email_returns_generic_success(api_client):
    response = api_client.post(
        f"{AUTH_URL}password-reset/",
        {"email": "unknown@example.com"},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert len(mail.outbox) == 0
    assert PasswordResetToken.objects.count() == 0


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_password_reset_confirm_happy_path_changes_password_and_consumes_token(
    api_client,
    account_user,
):
    active_refresh = RefreshToken.for_user(account_user)
    active_token_id = active_refresh["jti"]
    api_client.post(
        f"{AUTH_URL}password-reset/",
        {"email": account_user.email},
        format="json",
    )
    reset_url = mail.outbox[0].body.splitlines()[-1]
    raw_token = parse_qs(urlparse(reset_url).query)["token"][0]

    response = api_client.post(
        f"{AUTH_URL}password-reset/confirm/",
        {
            "token": raw_token,
            "password": RESET_PASSWORD,
            "password_confirm": RESET_PASSWORD,
        },
        format="json",
    )

    assert response.status_code == 200
    account_user.refresh_from_db()
    assert account_user.check_password(RESET_PASSWORD)
    assert PasswordResetToken.objects.get(user=account_user).is_used is True
    assert BlacklistedToken.objects.filter(token__jti=active_token_id).exists()


@pytest.mark.django_db
def test_password_reset_confirm_rejects_password_mismatch(api_client, account_user):
    raw_token = "valid-reset-token"
    reset_record = PasswordResetToken.objects.create(
        user=account_user,
        token_hash=PasswordResetToken.hash_token(raw_token),
    )

    response = api_client.post(
        f"{AUTH_URL}password-reset/confirm/",
        {
            "token": raw_token,
            "password": RESET_PASSWORD,
            "password_confirm": "A-different-password-853!",
        },
        format="json",
    )

    assert response.status_code == 400
    reset_record.refresh_from_db()
    assert reset_record.is_used is False
    account_user.refresh_from_db()
    assert account_user.check_password(STRONG_PASSWORD)


@pytest.mark.django_db
@pytest.mark.parametrize("raw_token", ["invalid-token", ""])
def test_password_reset_confirm_rejects_invalid_or_missing_token(
    api_client,
    account_user,
    raw_token,
):
    response = api_client.post(
        f"{AUTH_URL}password-reset/confirm/",
        {
            "token": raw_token,
            "password": RESET_PASSWORD,
            "password_confirm": RESET_PASSWORD,
        },
        format="json",
    )

    assert response.status_code == 400
    account_user.refresh_from_db()
    assert account_user.check_password(STRONG_PASSWORD)


@pytest.mark.django_db
def test_password_reset_confirm_rejects_expired_token(api_client, account_user):
    raw_token = "expired-reset-token"
    reset_record = PasswordResetToken.objects.create(
        user=account_user,
        token_hash=PasswordResetToken.hash_token(raw_token),
    )
    PasswordResetToken.objects.filter(pk=reset_record.pk).update(
        created_at=timezone.now() - timedelta(hours=25)
    )

    response = api_client.post(
        f"{AUTH_URL}password-reset/confirm/",
        {
            "token": raw_token,
            "password": RESET_PASSWORD,
            "password_confirm": RESET_PASSWORD,
        },
        format="json",
    )

    assert response.status_code == 400
    account_user.refresh_from_db()
    assert account_user.check_password(STRONG_PASSWORD)
