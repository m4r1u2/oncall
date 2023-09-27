# Generated by Django 3.2.20 on 2023-09-20 18:13

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('webhooks', '0010_alter_webhook_trigger_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='webhook',
            name='preset',
            field=models.CharField(blank=True, default=None, max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name='webhook',
            name='http_method',
            field=models.CharField(default='POST', max_length=32, null=True),
        ),
    ]
