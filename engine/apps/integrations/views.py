import json
import logging
from urllib.parse import urljoin

from django.apps import apps
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.utils import IntegrityError
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.template import loader
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django_sns_view.views import SNSEndpoint
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.alerts.models import AlertReceiveChannel
from apps.heartbeat.tasks import heartbeat_checkup, process_heartbeat_task
from apps.integrations.mixins import (
    AlertChannelDefiningMixin,
    BrowsableInstructionMixin,
    IntegrationHeartBeatRateLimitMixin,
    IntegrationRateLimitMixin,
    is_ratelimit_ignored,
)
from apps.integrations.tasks import create_alert, create_alertmanager_alerts
from apps.sendgridapp.parse import Parse
from apps.sendgridapp.permissions import AllowOnlySendgrid

logger = logging.getLogger(__name__)


class AmazonSNS(BrowsableInstructionMixin, SNSEndpoint):
    @method_decorator(csrf_exempt)
    def dispatch(self, *args, **kwargs):
        # Cleaning for SNSEndpoint
        args[0].alert_channel_key = kwargs["alert_channel_key"]
        del kwargs["alert_channel_key"]
        # For browserable API
        if args[0].method == "GET":
            args = (args[0], args[0].alert_channel_key)

        try:
            return super(SNSEndpoint, self).dispatch(*args, **kwargs)
        except Exception as e:
            print(e)
            return JsonResponse(status=400, data={})

    def handle_message(self, message, payload):
        try:
            alert_receive_channel = AlertReceiveChannel.objects.get(token=self.request.alert_channel_key)
        except AlertReceiveChannel.DoesNotExist:
            raise PermissionDenied("Integration key was not found. Permission denied.")

        if type(message) is str:
            try:
                message = json.loads(message)
            except json.JSONDecodeError:
                message = message
        if type(message) is dict:
            # Here we expect CloudWatch or Beanstack payload
            message_text = "*State: {}*\n".format(message.get("NewStateValue", "NO"))
            message_text += "Region: {}\n".format(message.get("Region", "Undefined"))
            if "AlarmDescription" in message and message.get("AlarmDescription"):
                message_text += "_Description:_ {}\n".format(message.get("AlarmDescription", "Undefined"))
            message_text += message.get("NewStateReason", "")

            region = payload.get("TopicArn").split(":")[3]
            if message.get("Trigger", {}).get("Namespace") == "AWS/ElasticBeanstalk":
                link_to_upstream = "https://console.aws.amazon.com/elasticbeanstalk/home?region={}".format(region)
            else:
                link_to_upstream = "https://console.aws.amazon.com/cloudwatch//home?region={}".format(region)

            raw_request_data = message
            title = message.get("AlarmName", "Alert")
        else:
            docs_amazon_sns_url = urljoin(settings.DOCS_URL, "/#/integrations/amazon_sns")
            title = "Alert"
            message_text = (
                "Non-JSON payload received. Please make sure you publish monitoring Alarms to SNS,"
                f" not logs: {docs_amazon_sns_url}\n" + message
            )
            link_to_upstream = None
            raw_request_data = {"message": message}

        create_alert.apply_async(
            [],
            {
                "title": title,
                "message": message_text,
                "image_url": None,
                "link_to_upstream_details": link_to_upstream,
                "alert_receive_channel_pk": alert_receive_channel.pk,
                "integration_unique_data": None,
                "raw_request_data": raw_request_data,
            },
        )


class AlertManagerAPIView(
    BrowsableInstructionMixin,
    AlertChannelDefiningMixin,
    IntegrationRateLimitMixin,
    APIView,
):
    def post(self, request, alert_receive_channel):
        """
        AlertManager requires super fast response so we create Alerts in Celery Task.
        Otherwise AlertManager raises `context deadline exceeded` exception.
        Unfortunately this HTTP timeout is not configurable on AlertManager's side.
        """
        if not self.check_integration_type(alert_receive_channel):
            return HttpResponseBadRequest(
                f"This url is for integration with {alert_receive_channel.get_integration_display()}. Key is for "
                + str(alert_receive_channel.get_integration_display())
            )

        for alert in request.data.get("alerts", []):
            if settings.DEBUG:
                create_alertmanager_alerts(alert_receive_channel.pk, alert)
            else:
                self.execute_rate_limit_with_notification_logic()

                if self.request.limited and not is_ratelimit_ignored(alert_receive_channel):
                    return self.get_ratelimit_http_response()

                create_alertmanager_alerts.apply_async((alert_receive_channel.pk, alert))

        return Response("Ok.")

    def check_integration_type(self, alert_receive_channel):
        return alert_receive_channel.integration == AlertReceiveChannel.INTEGRATION_ALERTMANAGER


class GrafanaAlertingAPIView(AlertManagerAPIView):
    """Grafana Alerting has the same payload structure as AlertManager"""

    def check_integration_type(self, alert_receive_channel):
        return alert_receive_channel.integration == AlertReceiveChannel.INTEGRATION_GRAFANA_ALERTING


class GrafanaAPIView(AlertManagerAPIView):
    """Support both new and old versions of Grafana Alerting"""

    def post(self, request, alert_receive_channel):
        # New Grafana has the same payload structure as AlertManager
        if "alerts" in request.data:
            return super().post(request, alert_receive_channel)

        """
        Example of request.data from old Grafana:
        {
            'evalMatches': [{
                'value': 100,
                'metric': 'High value',
                'tags': None
            }, {
                'value': 200,
                'metric': 'Higher Value',
                'tags': None
            }],
            'imageUrl': 'http://grafana.org/assets/img/blog/mixed_styles.png',
            'message': 'Someone is testing the alert notification within grafana.',
            'ruleId': 0,
            'ruleName': 'Test notification',
            'ruleUrl': 'http://localhost:3000/',
            'state': 'alerting',
            'title': '[Alerting] Test notification'
        }
        """
        if not self.check_integration_type(alert_receive_channel):
            return HttpResponseBadRequest(
                "This url is for integration with Grafana. Key is for "
                + str(alert_receive_channel.get_integration_display())
            )

        if "attachments" in request.data:
            # Fallback in case user by mistake configured Slack url instead of webhook
            """
            {
                "parse": "full",
                "channel": "#dev",
                "attachments": [
                    {
                    "ts": 1549259302,
                    "text": " ",
                    "color": "#D63232",
                    "title": "[Alerting] Test server RAM Usage alert",
                    "fields": [
                        {
                        "short": true,
                        "title": "System",
                        "value": 1563850717.2881355
                        }
                    ],
                    "footer": "Grafana v5.4.3",
                    "fallback": "[Alerting] Test server RAM Usage alert",
                    "image_url": "",
                    "title_link": "http://abc",
                    "footer_icon": "https://grafana.com/assets/img/fav32.png"
                    }
                ]
            }
            """
            attachment = request.data["attachments"][0]

            create_alert.apply_async(
                [],
                {
                    "title": attachment.get("title", "Title"),
                    "message": "_FYI: Misconfiguration detected. Please switch integration type from Slack to WebHook in "
                    "Grafana._\n_Integration URL: {} _\n\n".format(alert_receive_channel.integration_url)
                    + attachment.get("text", ""),
                    "image_url": attachment.get("image_url", None),
                    "link_to_upstream_details": attachment.get("title_link", None),
                    "alert_receive_channel_pk": alert_receive_channel.pk,
                    "integration_unique_data": json.dumps(
                        {
                            "evalMatches": [
                                {"metric": value["title"], "value": str(value["value"])}
                                for value in attachment["fields"]
                            ]
                        }
                    ),
                    "raw_request_data": request.data,
                },
            )
        else:
            create_alert.apply_async(
                [],
                {
                    "title": request.data.get("title", "Title"),
                    "message": request.data.get("message", None),
                    "image_url": request.data.get("imageUrl", None),
                    "link_to_upstream_details": request.data.get("ruleUrl", None),
                    "alert_receive_channel_pk": alert_receive_channel.pk,
                    "integration_unique_data": json.dumps({"evalMatches": request.data.get("evalMatches", [])}),
                    "raw_request_data": request.data,
                },
            )
        return Response("Ok.")

    def check_integration_type(self, alert_receive_channel):
        return alert_receive_channel.integration == AlertReceiveChannel.INTEGRATION_GRAFANA


class UniversalAPIView(BrowsableInstructionMixin, AlertChannelDefiningMixin, IntegrationRateLimitMixin, APIView):
    def post(self, request, alert_receive_channel, *args, **kwargs):
        if not alert_receive_channel.config.slug == kwargs["integration_type"]:
            return HttpResponseBadRequest(
                f"This url is for integration with {alert_receive_channel.config.title}."
                f"Key is for {alert_receive_channel.get_integration_display()}"
            )
        create_alert.apply_async(
            [],
            {
                "title": None,
                "message": None,
                "image_url": None,
                "link_to_upstream_details": None,
                "alert_receive_channel_pk": alert_receive_channel.pk,
                "integration_unique_data": None,
                "raw_request_data": request.data,
            },
        )
        return Response("Ok.")


# TODO: restore HeartBeatAPIView integration or clean it up as it is not used now
class HeartBeatAPIView(AlertChannelDefiningMixin, APIView):
    def get(self, request, alert_receive_channel):
        template = loader.get_template("heartbeat_link.html")
        docs_url = urljoin(settings.DOCS_URL, "/#/integrations/heartbeat")
        return HttpResponse(
            template.render(
                {
                    "docs_url": docs_url,
                }
            )
        )

    def post(self, request, alert_receive_channel):
        HeartBeat = apps.get_model("heartbeat", "HeartBeat")

        if request.data.get("action") == "activate":
            # timeout_seconds
            timeout_seconds = request.data.get("timeout_seconds")
            try:
                timeout_seconds = int(timeout_seconds)
            except ValueError:
                timeout_seconds = None

            if timeout_seconds is None:
                return Response(status=400, data="timeout_seconds int expected")
            # id
            _id = request.data.get("id", "default")
            # title
            title = request.data.get("title", "Title")
            # title
            link = request.data.get("link")
            # message
            message = request.data.get("message")

            heartbeat = HeartBeat(
                alert_receive_channel=alert_receive_channel,
                timeout_seconds=timeout_seconds,
                title=title,
                message=message,
                link=link,
                user_defined_id=_id,
                last_heartbeat_time=timezone.now(),
                last_checkup_task_time=timezone.now(),
                actual_check_up_task_id="none",
            )
            try:
                heartbeat.save()
                with transaction.atomic():
                    heartbeat = HeartBeat.objects.filter(pk=heartbeat.pk).select_for_update()[0]
                    task = heartbeat_checkup.apply_async(
                        (heartbeat.pk,),
                        countdown=heartbeat.timeout_seconds,
                    )
                    heartbeat.actual_check_up_task_id = task.id
                    heartbeat.save()
            except IntegrityError:
                return Response(status=400, data="id should be unique")

        elif request.data.get("action") == "deactivate":
            _id = request.data.get("id", "default")
            try:
                heartbeat = HeartBeat.objects.filter(
                    alert_receive_channel=alert_receive_channel,
                    user_defined_id=_id,
                ).get()
                heartbeat.delete()
            except HeartBeat.DoesNotExist:
                return Response(status=400, data="heartbeat not found")

        elif request.data.get("action") == "list":
            result = []
            heartbeats = HeartBeat.objects.filter(
                alert_receive_channel=alert_receive_channel,
            ).all()
            for heartbeat in heartbeats:
                result.append(
                    {
                        "created_at": heartbeat.created_at,
                        "last_heartbeat": heartbeat.last_heartbeat_time,
                        "expiration_time": heartbeat.expiration_time,
                        "is_expired": heartbeat.is_expired,
                        "id": heartbeat.user_defined_id,
                        "title": heartbeat.title,
                        "timeout_seconds": heartbeat.timeout_seconds,
                        "link": heartbeat.link,
                        "message": heartbeat.message,
                    }
                )
            return Response(result)

        elif request.data.get("action") == "heartbeat":
            _id = request.data.get("id", "default")
            with transaction.atomic():
                try:
                    heartbeat = HeartBeat.objects.filter(
                        alert_receive_channel=alert_receive_channel,
                        user_defined_id=_id,
                    ).select_for_update()[0]
                    task = heartbeat_checkup.apply_async(
                        (heartbeat.pk,),
                        countdown=heartbeat.timeout_seconds,
                    )
                    heartbeat.actual_check_up_task_id = task.id
                    heartbeat.last_heartbeat_time = timezone.now()
                    update_fields = ["actual_check_up_task_id", "last_heartbeat_time"]
                    state_changed = heartbeat.check_heartbeat_state()
                    if state_changed:
                        update_fields.append("previous_alerted_state_was_life")
                    heartbeat.save(update_fields=update_fields)
                except IndexError:
                    return Response(status=400, data="heartbeat not found")
        return Response("Ok.")


class InboundWebhookEmailView(AlertChannelDefiningMixin, APIView):
    permission_classes = [AllowOnlySendgrid]

    def dispatch(self, *args, **kwargs):
        parse = Parse(self.request)
        self.email_data = parse.key_values()
        # When email is forwarded recipient field can be stored both in "to" and in "envelope" fields.
        token_from_to = self._parse_token_from_to(self.email_data)
        try:
            kwargs["alert_channel_key"] = token_from_to
            return super().dispatch(*args, **kwargs)
        except KeyError as e:
            logger.warning(f"InboundWebhookEmailView: {e}")
        except PermissionDenied as e:
            self._log_permission_denied(token_from_to, e)
            kwargs.pop("alert_channel_key")

        token_from_envelope = self._parse_token_from_envelope(self.email_data)
        try:
            kwargs["alert_channel_key"] = token_from_envelope
            return super().dispatch(*args, **kwargs)
        except KeyError as e:
            logger.warning(f"InboundWebhookEmailView: {e}")
        except PermissionDenied as e:
            self._log_permission_denied(token_from_to, e)
            kwargs.pop("alert_channel_key")

        raise PermissionDenied("Integration key was not found. Permission denied.")

    def _log_permission_denied(self, token, e):
        logger.info(
            f"InboundWebhookEmailView: Permission denied. token {token}. "
            f"To {self.email_data.get('to')}. "
            f"Envelope {self.email_data.get('envelope')}."
            f"Exception: {e}"
        )

    def _parse_token_from_envelope(self, email_data):
        envelope = email_data["envelope"]
        envelope = json.loads(envelope)
        token = envelope.get("to")[0].split("@")[0]
        return token

    def _parse_token_from_to(self, email_data):
        return email_data["to"].split("@")[0]

    def post(self, request, alert_receive_channel=None):
        title = self.email_data["subject"]
        message = self.email_data.get("text", "").strip()

        payload = {"title": title, "message": message}

        if alert_receive_channel:
            create_alert.apply_async(
                [],
                {
                    "title": title,
                    "message": message,
                    "alert_receive_channel_pk": alert_receive_channel.pk,
                    "image_url": None,
                    "link_to_upstream_details": payload.get("link_to_upstream_details"),
                    "integration_unique_data": payload,
                    "raw_request_data": request.data,
                },
            )

        return Response("OK")


class IntegrationHeartBeatAPIView(AlertChannelDefiningMixin, IntegrationHeartBeatRateLimitMixin, APIView):
    def get(self, request, alert_receive_channel):
        self._process_heartbeat_signal(request, alert_receive_channel)
        return Response(":)")

    def post(self, request, alert_receive_channel):
        self._process_heartbeat_signal(request, alert_receive_channel)
        return Response(status=200)

    def _process_heartbeat_signal(self, request, alert_receive_channel):
        process_heartbeat_task.apply_async(
            (alert_receive_channel.pk,),
        )
