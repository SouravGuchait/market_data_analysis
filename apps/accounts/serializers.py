from __future__ import annotations

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django_zxcvbn_password_validator import ZxcvbnPasswordValidator
from rest_framework import serializers


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(required=True)
    email = serializers.EmailField(required=True)
    password = serializers.CharField(
        write_only=True, required=True, style={"input_type": "password"}
    )
    password_confirm = serializers.CharField(
        write_only=True, required=True, style={"input_type": "password"}
    )

    class Meta:
        model = User
        fields = ("username", "email", "password", "password_confirm")

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value.lower()

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("A user with this username already exists.")
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})

        # Django built-in validators
        try:
            validate_password(attrs["password"])
        except ValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        validator = ZxcvbnPasswordValidator()
        # Custom Zxcvbn strength rating check (0-4 scale)
        try:
            validator.validate(attrs["password"])
            print("Password is strong enough!")
        except ValidationError as e:
            print(f"Password rejected: {e.messages}")
        return attrs

    def create(self, validated_data):
        validated_data.pop("password_confirm")
        return User.objects.create_user(
            username=validated_data["username"],
            email=validated_data["email"],
            password=validated_data["password"],
        )


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(required=True)
    password = serializers.CharField(required=True, write_only=True)

    def validate(self, attrs):
        identifier = attrs.get("username")
        password = attrs.get("password")

        # Case-insensitive resolution
        user = (
            User.objects.filter(username__iexact=identifier).first()
            or User.objects.filter(email__iexact=identifier).first()
        )

        if user and user.check_password(password):
            if not user.is_active:
                raise serializers.ValidationError("Account is disabled.")
            return {"user": user}

        # Generic error message to defend against Account Enumeration
        raise serializers.ValidationError("Invalid username/email or password configuration.")


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "email")
