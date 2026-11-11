from django.urls import path
from .views import (
    RegisterView, LoginView, LogoutView, ThrottledTokenRefreshView, 
    CurrentUserView, PasswordResetRequestView, PasswordResetConfirmView
)

urlpatterns = [
    path("register/", RegisterView.as_view(), name="auth_register"),
    path("login/", LoginView.as_view(), name="auth_login"),
    path("token/refresh/", ThrottledTokenRefreshView.as_view(), name="token_refresh"),
    path("logout/", LogoutView.as_view(), name="auth_logout"),
    path("me/", CurrentUserView.as_view(), name="auth_current_user"),
    path("password-reset/", PasswordResetRequestView.as_view(), name="password_reset_request"),
    path("password-reset/confirm/", PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
]
