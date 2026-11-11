from __future__ import annotations

import secrets
from collections.abc import Mapping

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.http import JsonResponse
from rest_framework import status, views
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from apps.audit.models import AuditAction, AuditResourceType, AuditStatus
from apps.audit.services import record_audit_event

from .models import PasswordResetToken
from .serializers import LoginSerializer, RegisterSerializer, UserSerializer


# --- THROTTLED REFRESH VIEW ---
class ThrottledTokenRefreshView(TokenRefreshView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "anon_token_refresh"


class RegisterView(APIView):
    queryset = get_user_model().objects.all()
    permission_classes = [AllowAny]
    serializer_class = RegisterSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "anon_register"

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            user_value: object = serializer.save()
            if not isinstance(user_value, User):
                raise TypeError("Registration serializer returned an unexpected user type.")
            user = user_value
            record_audit_event(
                actor=user,
                action=AuditAction.REGISTRATION,
                resource_type=AuditResourceType.USER,
                resource_id=user.pk,
                status=AuditStatus.SUCCESS,
                request=request,
                metadata={"username": user.username},
            )
        return JsonResponse(
            {
                "success": True,
                "message": "Account created",
                "data": {
                    "username": user.username,
                    "email": user.email,
                },
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "anon_login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except DRFValidationError:
            record_audit_event(
                actor=None,
                action=AuditAction.LOGIN_FAILED,
                resource_type=AuditResourceType.USER,
                status=AuditStatus.FAILURE,
                request=request,
                metadata={"reason": "authentication_failed"},
            )
            raise
        validated_data: object = serializer.validated_data
        if not isinstance(validated_data, Mapping):
            raise TypeError("Login serializer returned invalid data.")
        user_value = validated_data.get("user")
        if not isinstance(user_value, AbstractBaseUser):
            raise TypeError("Login serializer did not return an authenticated user.")
        user = user_value

        refresh = RefreshToken.for_user(user)
        record_audit_event(
            actor=user,
            action=AuditAction.LOGIN_SUCCESS,
            resource_type=AuditResourceType.USER,
            resource_id=user.pk,
            status=AuditStatus.SUCCESS,
            request=request,
            metadata={"username": user.get_username()},
        )
        return JsonResponse(
            {
                "success": True,
                "message": "Login successful",
                "data": {
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                },
                "errors": None,
            },
            status=status.HTTP_200_OK,
        )


class LogoutView(views.APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get("refresh")
            token = RefreshToken(refresh_token)
            token.blacklist()  # Explicit blacklisting requirement
        except Exception:
            record_audit_event(
                actor=request.user,
                action=AuditAction.LOGOUT,
                resource_type=AuditResourceType.USER,
                resource_id=request.user.pk,
                status=AuditStatus.FAILURE,
                request=request,
                metadata={"reason": "invalid_or_blacklisted_token"},
            )
            return JsonResponse(
                {"detail": "Invalid or already blacklisted token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        record_audit_event(
            actor=request.user,
            action=AuditAction.LOGOUT,
            resource_type=AuditResourceType.USER,
            resource_id=request.user.pk,
            status=AuditStatus.SUCCESS,
            request=request,
        )
        return JsonResponse(
            {"detail": "Successfully logged out."}, status=status.HTTP_205_RESET_CONTENT
        )


class CurrentUserView(views.APIView):
    queryset = get_user_model().objects.all()
    permission_classes = [IsAuthenticated]
    serializer_class = UserSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "user_profile"

    def get(self, request):
        return self.request.user


class PasswordResetRequestView(views.APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "anon_password_reset"

    def post(self, request):
        email = request.data.get("email", "").strip()
        user = get_user_model().objects.filter(email__iexact=email).first()

        # Always execute code flow securely to prevent execution timing attacks
        raw_token = secrets.token_urlsafe(32)
        token_hash = PasswordResetToken.hash_token(raw_token)

        if user:
            # Revoke existing unused reset tokens for safety
            PasswordResetToken.objects.filter(user=user, is_used=False).update(is_used=True)

            # Store only the token hash
            PasswordResetToken.objects.create(user=user, token_hash=token_hash)

            # Fire email pipeline asynchronously or via standard SMTP
            reset_link = f"{settings.FRONTEND_RESET_URL}?token={raw_token}"
            try:
                send_mail(
                    subject="Password Reset Request",
                    message=f"Click the link below to securely reset your credentials:\n{reset_link}",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[email],
                    fail_silently=False,
                )
            except Exception:
                record_audit_event(
                    actor=user,
                    action=AuditAction.PASSWORD_RESET_REQUESTED,
                    resource_type=AuditResourceType.USER,
                    resource_id=user.pk,
                    status=AuditStatus.FAILURE,
                    request=request,
                    metadata={"reason": "email_delivery_failed"},
                )
                raise
            record_audit_event(
                actor=user,
                action=AuditAction.PASSWORD_RESET_REQUESTED,
                resource_type=AuditResourceType.USER,
                resource_id=user.pk,
                status=AuditStatus.SUCCESS,
                request=request,
            )
        else:
            record_audit_event(
                actor=None,
                action=AuditAction.PASSWORD_RESET_REQUESTED,
                resource_type=AuditResourceType.USER,
                status=AuditStatus.SUCCESS,
                request=request,
                metadata={"account_matched": False},
            )

        # Generic JsonResponse prevents Account Enumeration
        return JsonResponse(
            {
                "success": True,
                "message": "If the email profile matches our records, a secure recovery tracking link has been issued.",
                "data": None,
                "errors": None,
            },
            status=status.HTTP_200_OK,
        )


def _validate_reset_request(
    token: object,
    password: object,
    password_confirm: object,
) -> tuple[PasswordResetToken | None, str | list[str] | None]:
    reset_record = None
    error_message = None
    if not isinstance(token, str) or not isinstance(password, str) or not password:
        error_message = "Missing execution payload data parameters."
    elif password != password_confirm:
        error_message = "Password confirmations do not match."
    else:
        token_hash = PasswordResetToken.hash_token(token)
        reset_record = PasswordResetToken.objects.filter(
            token_hash=token_hash, is_used=False
        ).first()
        if reset_record is None or reset_record.is_expired:
            reset_record = None
            error_message = "Invalid, consumed, or expired signature token mapping."
        else:
            try:
                validate_password(password)
            except ValidationError as exc:
                reset_record = None
                error_message = exc.messages
    return reset_record, error_message


class PasswordResetConfirmView(views.APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "anon_password_reset_confirm"

    def post(self, request):
        new_password = request.data.get("password")
        reset_record, error_message = _validate_reset_request(
            request.data.get("token"),
            request.data.get("password"),
            request.data.get("password_confirm"),
        )
        if error_message is not None:
            return JsonResponse(
                {"success": False, "message": error_message},
                status=status.HTTP_400_BAD_REQUEST,
            )

        assert reset_record is not None
        with transaction.atomic():
            user = reset_record.user
            user.set_password(new_password)
            user.save()
            reset_record.is_used = True
            reset_record.save()

            tokens = OutstandingToken.objects.filter(user=user)
            revoked_count = 0
            for outstanding_token in tokens:
                _, created = BlacklistedToken.objects.get_or_create(token=outstanding_token)
                revoked_count += int(created)

            record_audit_event(
                actor=user,
                action=AuditAction.PASSWORD_RESET_COMPLETED,
                resource_type=AuditResourceType.USER,
                resource_id=user.pk,
                status=AuditStatus.SUCCESS,
                request=request,
                metadata={"revoked_token_count": revoked_count},
            )

        return JsonResponse(
            {
                "success": True,
                "message": "Credentials updated successfully. Active tokens invalidated.",
            },
            status=status.HTTP_200_OK,
        )
