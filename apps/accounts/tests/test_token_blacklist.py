"""Persistence tests for revoked JWT refresh tokens."""

from __future__ import annotations

from typing import cast

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.views import LogoutView, PasswordResetConfirmView
from apps.accounts.models import PasswordResetToken


@pytest.fixture
def account_user(db):
    user_model = cast(type[User], get_user_model())
    return user_model.objects.create_user(
        username="blacklist-user",
        email="blacklist@example.com",
        password="Original!Password-8214",
    )


@pytest.mark.django_db
def test_logout_persists_refresh_token_in_blacklist(account_user):
    refresh = RefreshToken.for_user(account_user)
    token_id = refresh["jti"]
    request = APIRequestFactory().post(
        "/logout/",
        {"refresh": str(refresh)},
        format="json",
    )
    force_authenticate(request, user=account_user)

    response = LogoutView.as_view(throttle_classes=[])(request)

    assert response.status_code == 205
    assert BlacklistedToken.objects.filter(token__jti=token_id).exists()
    with pytest.raises(TokenError):
        RefreshToken(cast(RefreshToken, str(refresh)))


@pytest.mark.django_db
def test_refresh_rotation_blacklists_old_token_in_database(account_user):
    refresh = RefreshToken.for_user(account_user)
    token_id = refresh["jti"]

    serializer = TokenRefreshSerializer(data={"refresh": str(refresh)})
    assert serializer.is_valid(), serializer.errors

    assert BlacklistedToken.objects.filter(token__jti=token_id).exists()
    replay = TokenRefreshSerializer(data={"refresh": str(refresh)})
    with pytest.raises(TokenError, match="blacklisted"):
        replay.is_valid(raise_exception=True)


@pytest.mark.django_db
def test_password_reset_persists_blacklist_for_all_user_refresh_tokens(account_user):
    refresh = RefreshToken.for_user(account_user)
    token_id = refresh["jti"]
    reset_token = "single-use-reset-token"
    PasswordResetToken.objects.create(
        user=account_user,
        token_hash=PasswordResetToken.hash_token(reset_token),
    )
    request = APIRequestFactory().post(
        "/password-reset/confirm/",
        {
            "token": reset_token,
            "password": "Replacement!Password-9147",
            "password_confirm": "Replacement!Password-9147",
        },
        format="json",
    )

    response = PasswordResetConfirmView.as_view(throttle_classes=[])(request)

    assert response.status_code == 200
    assert BlacklistedToken.objects.filter(token__jti=token_id).exists()
    with pytest.raises(TokenError):
        RefreshToken(cast(RefreshToken, str(refresh)))
