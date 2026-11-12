"""Seed exchange master data, then the generator instrument catalog."""

from __future__ import annotations

from argparse import ArgumentParser

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Seed exchanges first, then instruments. Safe to run repeatedly."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--count",
            type=int,
            default=None,
            help="Instrument listings to seed after exchanges. Default: the symbol pool.",
        )

    def handle(self, *args: object, **options: object) -> None:
        call_command("seed_exchanges")
        count = options.get("count")
        if isinstance(count, int):
            call_command("seed_instruments", count=count)
            return
        call_command("seed_instruments")
