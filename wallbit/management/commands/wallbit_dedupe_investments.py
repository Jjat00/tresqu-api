"""Remove optimistic Investment rows superseded by a mirrored (synced) row.

Background: a trade placed through the agent used to create an immediate,
optimistic ``Investment`` with ``wallbit_tx=NULL`` (and no shares). The periodic
Wallbit sync then created a *second* row for the same trade, this one linked to
the ``WallbitTxMirror``. Result: one real trade shown twice — and the duplicate
inflated "capital invertido" against a live value that didn't include it,
producing a false loss.

The write path no longer creates that optimistic row, and the sync now adopts
any stray unlinked row instead of duplicating it. This command cleans up rows
that were already duplicated before the fix shipped.

A row is a removable duplicate when it is unlinked (``wallbit_tx IS NULL``) and
there exists another row for the SAME (user, kind, action, symbol,
chest_category, amount_usd) that IS linked to a mirror. The linked row is the
source of truth (it carries the real shares + settlement status), so the
unlinked one is dropped.

Dry-run by default — pass ``--apply`` to actually delete.
"""
from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Count

from wallbit.models import Investment


class Command(BaseCommand):
    help = "Delete optimistic Investment duplicates superseded by a synced (mirrored) row."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually delete the duplicates (default is a dry-run).",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            default=None,
            help="Restrict to a single user id.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        user_id = options["user_id"]

        unlinked = Investment.objects.filter(wallbit_tx__isnull=True)
        if user_id:
            unlinked = unlinked.filter(user_id=user_id)

        to_delete: list[Investment] = []
        for inv in unlinked.select_related("user"):
            amount_2dp = (inv.amount_usd or Decimal(0)).quantize(Decimal("0.01"))
            siblings = Investment.objects.filter(
                user_id=inv.user_id,
                kind=inv.kind,
                action=inv.action,
                symbol__iexact=inv.symbol,
                chest_category=inv.chest_category,
                amount_usd=amount_2dp,
                wallbit_tx__isnull=False,
            )
            if siblings.exists():
                to_delete.append(inv)

        if not to_delete:
            self.stdout.write(self.style.SUCCESS("No duplicate optimistic rows found."))
            return

        self.stdout.write(
            self.style.WARNING(f"Found {len(to_delete)} duplicate optimistic row(s):")
        )
        for inv in to_delete:
            self.stdout.write(
                f"  - id={inv.id} user={inv.user_id} {inv.action} {inv.kind} "
                f"{inv.symbol or inv.chest_category or '-'} ${inv.amount_usd} "
                f"shares={inv.shares} created_at={inv.created_at:%Y-%m-%d %H:%M}"
            )

        if not apply:
            self.stdout.write(
                self.style.NOTICE("Dry-run — nothing deleted. Re-run with --apply to delete.")
            )
            return

        ids = [inv.id for inv in to_delete]
        deleted, _ = Investment.objects.filter(id__in=ids).delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} row(s)."))
