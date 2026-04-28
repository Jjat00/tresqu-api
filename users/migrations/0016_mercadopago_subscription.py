from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0015_monthlyusage'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscription',
            name='mp_preapproval_id',
            field=models.CharField(
                blank=True, max_length=100, null=True, unique=True,
                help_text='Mercado Pago preapproval (subscription) id'),
        ),
        migrations.AddField(
            model_name='subscription',
            name='mp_status',
            field=models.CharField(
                blank=True, null=True, max_length=20,
                choices=[
                    ('pending', 'Pending'),
                    ('authorized', 'Authorized'),
                    ('paused', 'Paused'),
                    ('cancelled', 'Cancelled'),
                    ('finished', 'Finished'),
                ]),
        ),
        migrations.AddField(
            model_name='subscription',
            name='next_payment_date',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='subscription',
            name='card_last_four',
            field=models.CharField(blank=True, max_length=4, null=True),
        ),
        migrations.CreateModel(
            name='MercadoPagoPlan',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True, primary_key=True, serialize=False)),
                ('is_yearly', models.BooleanField(default=False)),
                ('preapproval_plan_id',
                 models.CharField(max_length=100, unique=True)),
                ('currency_id', models.CharField(default='COP', max_length=3)),
                ('transaction_amount', models.DecimalField(
                    decimal_places=2, max_digits=12)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('subscription_plan', models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name='mp_plans',
                    to='users.subscriptionplan')),
            ],
            options={
                'unique_together': {
                    ('subscription_plan', 'is_yearly', 'currency_id')
                },
            },
        ),
    ]
