"""Add Investment.executed_at and backfill from WallbitTxMirror.created_at_wallbit.

The previous schema only stored Investment.created_at (auto_now_add), which
recorded *when the sync ran*, not when the user actually traded. As a result
the portfolio timeline collapsed every historical trade onto the day of the
first sync. This migration introduces a separate executed_at field and
backfills it from the mirrored Wallbit transaction date.
"""
from django.db import migrations, models


def backfill_executed_at(apps, schema_editor):
    Investment = apps.get_model("wallbit", "Investment")
    to_update = []
    qs = Investment.objects.select_related("wallbit_tx").only(
        "id", "executed_at", "created_at", "wallbit_tx"
    )
    for inv in qs.iterator():
        if inv.executed_at:
            continue
        wallbit_dt = getattr(inv.wallbit_tx, "created_at_wallbit", None) if inv.wallbit_tx_id else None
        inv.executed_at = wallbit_dt or inv.created_at
        to_update.append(inv)
        if len(to_update) >= 500:
            Investment.objects.bulk_update(to_update, ["executed_at"])
            to_update.clear()
    if to_update:
        Investment.objects.bulk_update(to_update, ["executed_at"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("wallbit", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="investment",
            name="executed_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddIndex(
            model_name="investment",
            index=models.Index(
                fields=["user", "-executed_at"],
                name="wallbit_inv_user_exec_idx",
            ),
        ),
        migrations.RunPython(backfill_executed_at, noop_reverse),
    ]
