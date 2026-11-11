"""Integration tests proving existing user flows create audit records."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import override_settings

from apps.accounts.models import PasswordResetToken
from apps.audit.models import AuditAction, AuditEvent, AuditStatus
from apps.portfolios.models import Portfolio

AUTH_URL = "/api/v1/auth/"
PORTFOLIOS_URL = "/api/v1/portfolios/"
STRONG_PASSWORD = "Violet!River_Orbit-Glass_9274"
RESET_PASSWORD = "New!Amber_Constellation-Paper_6842"


@pytest.mark.django_db
def test_registration_records_an_audit_event(api_client):
    response = api_client.post(
        f"{AUTH_URL}register/",
        {
            "username": "new-audit-user",
            "email": "new-audit-user@example.com",
            "password": STRONG_PASSWORD,
            "password_confirm": STRONG_PASSWORD,
        },
        format="json",
    )

    assert response.status_code == 201
    created = User.objects.get(username="new-audit-user")
    event = AuditEvent.objects.get(action=AuditAction.REGISTRATION)
    assert event.actor == created
    assert event.resource_type == "user"
    assert event.resource_id == str(created.pk)
    assert event.status == AuditStatus.SUCCESS
    assert event.metadata == {"username": "new-audit-user"}
    assert event.request_id == response["X-Request-ID"]
    assert event.source_ip == "127.0.0.1"


@pytest.mark.django_db
def test_login_success_records_an_audit_event(api_client, user):
    response = api_client.post(
        f"{AUTH_URL}login/",
        {"username": user.username, "password": "Audit!Test-Password_9274"},
        format="json",
    )

    assert response.status_code == 200
    event = AuditEvent.objects.get(action=AuditAction.LOGIN_SUCCESS)
    assert event.actor == user
    assert event.status == AuditStatus.SUCCESS
    assert event.metadata == {"username": user.username}


@pytest.mark.django_db
def test_login_failure_records_an_unattributed_event(api_client, user):
    response = api_client.post(
        f"{AUTH_URL}login/",
        {"username": user.username, "password": "wrong-password"},
        format="json",
    )

    assert response.status_code == 400
    event = AuditEvent.objects.get(action=AuditAction.LOGIN_FAILED)
    assert event.actor is None
    assert event.status == AuditStatus.FAILURE
    assert event.metadata == {"reason": "authentication_failed"}


@pytest.mark.django_db
def test_logout_records_an_audit_event(auth_client, user):
    from rest_framework_simplejwt.tokens import RefreshToken

    refresh = RefreshToken.for_user(user)

    response = auth_client.post(
        f"{AUTH_URL}logout/",
        {"refresh": str(refresh)},
        format="json",
    )

    assert response.status_code == 205
    event = AuditEvent.objects.get(action=AuditAction.LOGOUT)
    assert event.actor == user
    assert event.status == AuditStatus.SUCCESS


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_password_reset_request_and_completion_are_recorded(api_client, user):
    request_response = api_client.post(
        f"{AUTH_URL}password-reset/",
        {"email": user.email},
        format="json",
    )
    assert request_response.status_code == 200

    requested_event = AuditEvent.objects.get(action=AuditAction.PASSWORD_RESET_REQUESTED)
    assert requested_event.actor == user
    assert requested_event.status == AuditStatus.SUCCESS

    raw_token = "audit-reset-token"
    PasswordResetToken.objects.create(
        user=user,
        token_hash=PasswordResetToken.hash_token(raw_token),
    )

    confirm_response = api_client.post(
        f"{AUTH_URL}password-reset/confirm/",
        {
            "token": raw_token,
            "password": RESET_PASSWORD,
            "password_confirm": RESET_PASSWORD,
        },
        format="json",
    )

    assert confirm_response.status_code == 200
    completed_event = AuditEvent.objects.get(action=AuditAction.PASSWORD_RESET_COMPLETED)
    assert completed_event.actor == user
    assert completed_event.status == AuditStatus.SUCCESS
    assert "revoked_token_count" in completed_event.metadata


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_password_reset_request_for_unknown_email_is_unattributed(api_client):
    response = api_client.post(
        f"{AUTH_URL}password-reset/",
        {"email": "missing@example.com"},
        format="json",
    )

    assert response.status_code == 200
    event = AuditEvent.objects.get(action=AuditAction.PASSWORD_RESET_REQUESTED)
    assert event.actor is None
    assert event.metadata == {"account_matched": False}


@pytest.mark.django_db
def test_portfolio_crud_and_membership_changes_are_recorded(auth_client, user, instrument):
    created = auth_client.post(
        PORTFOLIOS_URL,
        {"name": "Audit Portfolio", "description": "tracked"},
        format="json",
    )
    assert created.status_code == 201
    portfolio_id = created.json()["data"]["id"]

    updated = auth_client.patch(
        f"{PORTFOLIOS_URL}{portfolio_id}/",
        {"name": "Renamed Portfolio"},
        format="json",
    )
    assert updated.status_code == 200

    membership = auth_client.post(
        f"{PORTFOLIOS_URL}{portfolio_id}/instruments/",
        {"instrument_id": instrument.pk},
        format="json",
    )
    assert membership.status_code == 201
    membership_id = membership.json()["data"]["id"]

    removed = auth_client.delete(f"{PORTFOLIOS_URL}{portfolio_id}/instruments/{membership_id}/")
    assert removed.status_code == 204

    deleted = auth_client.delete(f"{PORTFOLIOS_URL}{portfolio_id}/")
    assert deleted.status_code == 204

    events = AuditEvent.objects.filter(actor=user).order_by("created_at")
    assert {event.action for event in events} == {
        AuditAction.PORTFOLIO_CREATED,
        AuditAction.PORTFOLIO_UPDATED,
        AuditAction.PORTFOLIO_INSTRUMENT_ADDED,
        AuditAction.PORTFOLIO_INSTRUMENT_REMOVED,
        AuditAction.PORTFOLIO_DELETED,
    }
    assert all(event.status == AuditStatus.SUCCESS for event in events)
    assert not Portfolio.objects.filter(pk=portfolio_id).exists()

    update_event = events.get(action=AuditAction.PORTFOLIO_UPDATED)
    assert update_event.metadata == {"changed_fields": ["name"]}

    membership_event = events.get(action=AuditAction.PORTFOLIO_INSTRUMENT_ADDED)
    assert membership_event.metadata["portfolio_id"] == portfolio_id
    assert membership_event.metadata["instrument_symbol"] == instrument.symbol
