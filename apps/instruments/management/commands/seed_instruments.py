"""Idempotently load instrument master records from the generator catalog."""

from __future__ import annotations

from argparse import ArgumentParser

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.instruments.services import InstrumentSeedError, seed_instrument_records


class Command(BaseCommand):
    help = "Create the generator instrument catalog and refresh seed-controlled metadata."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--count",
            type=int,
            default=None,
            help="Listings to seed. Default: one listing for every symbol in the pool.",
        )

    @transaction.atomic
    def handle(self, *args: object, **options: object) -> None:
        count = options.get("count")
        if isinstance(count, int) and count < 1:
            raise CommandError("--count must be at least 1.")
        resolved = count if isinstance(count, int) else None
        try:
            created, updated, unchanged = seed_instrument_records(resolved)
        except InstrumentSeedError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                "Instrument seed complete; "
                f"created {created}, updated {updated}, unchanged {unchanged}."
            )
        )
