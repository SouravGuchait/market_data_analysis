"""Operational command that verifies external dependencies."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.common.health import run_readiness_checks
from config.celery import app as celery_app


class Command(BaseCommand):
    help = "Check MySQL, Redis cache and Celery broker connectivity."

    def handle(self, *args: object, **options: object) -> None:
        failures: list[str] = []

        for name, result in run_readiness_checks().checks.items():
            if result.status == "ok":
                self.stdout.write(self.style.SUCCESS(f"{name}: ok"))
            else:
                failures.append(name)
                self.stderr.write(self.style.ERROR(f"{name}: unavailable"))

        connection = celery_app.connection_for_write()
        try:
            connection.ensure_connection(max_retries=1)
        except Exception as exc:
            failures.append("celery_broker")
            self.stderr.write(
                self.style.ERROR(f"celery_broker: unavailable ({type(exc).__name__})")
            )
        else:
            self.stdout.write(self.style.SUCCESS("celery_broker: ok"))
        finally:
            connection.release()

        if failures:
            raise CommandError("Dependency check failed: " + ", ".join(sorted(failures)))
