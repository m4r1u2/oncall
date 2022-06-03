# Generated by Django 3.2.5 on 2022-05-31 14:46

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('user_management', '0001_squashed_initial'),
        ('slack', '0001_squashed_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='slackmessage',
            name='organization',
            field=models.ForeignKey(default=None, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='slack_message', to='user_management.organization'),
        ),
        migrations.AddField(
            model_name='slackchannel',
            name='slack_team_identity',
            field=models.ForeignKey(default=None, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='cached_channels', to='slack.slackteamidentity'),
        ),
        migrations.AddField(
            model_name='slackactionrecord',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='actions', to='user_management.organization'),
        ),
        migrations.AddField(
            model_name='slackactionrecord',
            name='user',
            field=models.ForeignKey(default=None, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='actions', to='user_management.user'),
        ),
        migrations.AddConstraint(
            model_name='slackuseridentity',
            constraint=models.UniqueConstraint(fields=('slack_id', 'slack_team_identity', 'counter'), name='unique_slack_identity_per_team'),
        ),
        migrations.AlterUniqueTogether(
            name='slackusergroup',
            unique_together={('slack_id', 'slack_team_identity')},
        ),
        migrations.AddConstraint(
            model_name='slackmessage',
            constraint=models.UniqueConstraint(fields=('slack_id', 'channel_id', '_slack_team_identity'), name='unique slack_id'),
        ),
        migrations.AlterUniqueTogether(
            name='slackchannel',
            unique_together={('slack_id', 'slack_team_identity')},
        ),
    ]
