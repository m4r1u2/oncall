# Generated by Django 3.2.5 on 2021-08-04 10:45

import sys
from django.db import migrations
from django.utils import timezone
from apps.public_api import constants as public_api_constants


STEP_WAIT = 0
STEP_NOTIFY = 1
NOTIFY_BY_SMS = 1
NOTIFY_BY_PHONE = 2
FIVE_MINUTES = timezone.timedelta(minutes=5)


def create_demo_token_instances(apps, schema_editor):
    if not (len(sys.argv) > 1 and sys.argv[1] == 'test'):
        User = apps.get_model('user_management', 'User')
        UserNotificationPolicy = apps.get_model("base", "UserNotificationPolicy")

        user = User.objects.get(public_primary_key=public_api_constants.DEMO_USER_ID)

        UserNotificationPolicy.objects.get_or_create(
            public_primary_key=public_api_constants.DEMO_PERSONAL_NOTIFICATION_ID_1,
            defaults=dict(
                important=False,
                user=user,
                notify_by=NOTIFY_BY_SMS,
                step=STEP_NOTIFY,
                order=0,
            )
        )
        UserNotificationPolicy.objects.get_or_create(
            public_primary_key=public_api_constants.DEMO_PERSONAL_NOTIFICATION_ID_2,
            defaults=dict(
                important=False,
                user=user,
                step=STEP_WAIT,
                wait_delay=FIVE_MINUTES,
                order=1,
            )
        )
        UserNotificationPolicy.objects.get_or_create(
            public_primary_key=public_api_constants.DEMO_PERSONAL_NOTIFICATION_ID_3,
            defaults=dict(
                important=False,
                user=user,
                step=STEP_NOTIFY,
                notify_by=NOTIFY_BY_PHONE,
                order=2,
            )
        )

        UserNotificationPolicy.objects.get_or_create(
            public_primary_key=public_api_constants.DEMO_PERSONAL_NOTIFICATION_ID_4,
            defaults=dict(
                important=True,
                user=user,
                notify_by=NOTIFY_BY_PHONE,
                order=0,
            )
        )


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0002_squashed_initial'),
        ('user_management', '0002_squashed_create_demo_token_instances')
    ]

    operations = [
        migrations.RunPython(create_demo_token_instances, migrations.RunPython.noop)
    ]
