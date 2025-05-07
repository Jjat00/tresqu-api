from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('categories', '0003_category_color'),
    ]

    operations = [
        migrations.AlterField(
            model_name='category',
            name='color',
            field=models.CharField(default='#CCCCCC', max_length=7),
        ),
    ]
