from django.db import migrations, models


def backfill_status(apps, schema_editor):
    """Derive the new lifecycle status from the legacy flags.

    Every row that already has an outcome (executed, error, cancelled) becomes
    non-pending, so decisions that failed before this release can no longer be
    re-confirmed from an old chat message.
    """
    AgentDecision = apps.get_model("wallbit", "AgentDecision")
    AgentDecision.objects.filter(executed=True).update(status="executed")
    AgentDecision.objects.filter(executed=False, error="cancelled_by_user").update(
        status="cancelled"
    )
    AgentDecision.objects.filter(executed=False).exclude(error="").exclude(
        status="cancelled"
    ).update(status="failed")
    # Confirmed but never resolved (crash mid-flight before this release):
    # nothing can settle it now, and it must not stay confirmable.
    AgentDecision.objects.filter(
        executed=False, error="", confirmed_at__isnull=False
    ).update(status="failed", error="unresolved_before_status_field")


class Migration(migrations.Migration):

    dependencies = [
        ("wallbit", "0003_rename_wallbit_inv_user_exec_idx_wallbit_inv_user_id_42e5ea_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentdecision",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("executing", "Executing"),
                    ("executed", "Executed"),
                    ("failed", "Failed"),
                    ("cancelled", "Cancelled"),
                    ("uncertain", "Uncertain"),
                ],
                db_index=True,
                default="pending",
                max_length=12,
            ),
        ),
        migrations.RunPython(backfill_status, migrations.RunPython.noop),
    ]
