import base64
import hashlib
import hmac
import json
import logging

import requests

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request

_logger = logging.getLogger(__name__)


class LineWebhookInvoice(http.Controller):

    @http.route('/line/webhook/invoice', type='http', auth='public', methods=['POST'], csrf=False)
    def line_webhook_invoice(self, **kwargs):
        config = request.env['ir.config_parameter'].sudo()
        channel_secret = config.get_param('aacc_line.channel_secret', '')

        body = request.httprequest.data
        signature = request.httprequest.headers.get('X-Line-Signature', '')

        if not channel_secret:
            _logger.error("No LINE Channel Secret configured")
            return request.make_response("No Channel Secret configured", status=500)

        expected = base64.b64encode(
            hmac.new(channel_secret.encode('utf-8'), body, hashlib.sha256).digest()
        ).decode('utf-8')

        if not hmac.compare_digest(signature, expected):
            _logger.warning("Invalid LINE webhook signature")
            return request.make_response("Invalid Signature", status=401)

        try:
            data = json.loads(body.decode('utf-8'))
        except json.JSONDecodeError:
            _logger.exception("Invalid JSON payload on LINE invoice webhook")
            return request.make_response("Bad Request", status=400)

        access_token = config.get_param('aacc_line.access_token', '')
        if not access_token:
            _logger.error("No LINE Access Token configured")
            return request.make_response("No Access Token configured", status=500)

        for event in data.get('events', []):
            if event.get('type') == 'message' and event.get('message', {}).get('type') == 'image':
                try:
                    self._process_image_event(event, access_token)
                except Exception:
                    _logger.exception("Unexpected error while processing invoice LINE image event")

        return request.make_response("OK")

    def _process_image_event(self, event, access_token):
        user_id = event.get('source', {}).get('userId')
        reply_token = event.get('replyToken')
        message_id = event.get('message', {}).get('id')

        if not message_id:
            _logger.warning("Missing message id in LINE event")
            return
        if not user_id:
            _logger.warning("Missing source.userId in LINE event")
            return

        invoice = request.env['account.move'].sudo().search([
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('line_user_id', '=', user_id),
            ('is_active_line_bill', '=', True),
            ('payment_state', 'not in', ('paid', 'in_payment')),
        ], limit=1)

        if not invoice:
            self._notify_line(
                access_token,
                user_id,
                "Oops! We couldn't find any pending invoice for this LINE account. If you believe this is an error, please contact our support. 💬",
                reply_token=reply_token,
            )
            return

        self._notify_line(
            access_token,
            user_id,
            f"Thank you! 🧾 We've received your slip for invoice {invoice.name}. Please wait a moment while we verify it for you. ⏳",
            reply_token=reply_token,
        )
        reply_token = None

        invoice.write({'line_payment_state': 'slip_received'})

        image_binary = self._download_line_image(access_token, message_id)
        if not image_binary:
            invoice.write({'line_payment_state': 'rejected'})
            self._notify_line(
                access_token,
                user_id,
                "Oops! We couldn't download the slip image. Could you please send it again? 🔄",
                reply_token=reply_token,
            )
            return

        invoice.write({
            'slipok_image': base64.b64encode(image_binary),
            'slipok_image_filename': f'slip_{invoice.name}.jpg',
            'line_payment_state': 'verifying',
        })

        try:
            invoice._action_slipok_verify_and_register_payment()
        except UserError as exc:
            message = (exc.args and exc.args[0]) or str(exc)
            invoice.write({'line_payment_state': 'rejected'})
            self._notify_line(access_token, user_id, f"Unfortunately, we couldn't verify your payment slip. ❌\nReason: {message}\nPlease check and try again.", reply_token=reply_token)
            return
        except Exception:
            _logger.exception("Invoice SlipOK verification failed unexpectedly")
            invoice.write({'line_payment_state': 'rejected'})
            self._notify_line(
                access_token,
                user_id,
                "We encountered a system error while verifying your slip. Please kindly contact our administrator for assistance. 🛠️",
                reply_token=reply_token,
            )
            return

        invoice.write({'line_payment_state': 'verified', 'is_active_line_bill': False})
        self._notify_line(
            access_token,
            user_id,
            f"Payment Verified! ✅\nThank you for your payment for invoice {invoice.name}. Your payment is now successfully recorded! 🎉",
            reply_token=reply_token,
        )

    def _download_line_image(self, access_token, message_id):
        url = f'https://api-data.line.me/v2/bot/message/{message_id}/content'
        headers = {'Authorization': f'Bearer {access_token}'}
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            return response.content

        _logger.error(
            "Failed to download LINE image. status=%s body=%s",
            response.status_code,
            response.text,
        )
        return None

    def _notify_line(self, token, line_user_id, text_message, reply_token=None):
        if reply_token and self._reply_line(token, reply_token, text_message):
            return True
        return self._push_line(token, line_user_id, text_message)

    def _reply_line(self, token, reply_token, text_message):
        url = 'https://api.line.me/v2/bot/message/reply'
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        }
        payload = {
            'replyToken': reply_token,
            'messages': [{'type': 'text', 'text': text_message}],
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code == 200:
                return True
            _logger.error("LINE reply failed. status=%s body=%s", response.status_code, response.text)
            return False
        except Exception:
            _logger.exception("Failed to send LINE reply")
            return False

    def _push_line(self, token, line_user_id, text_message):
        if not line_user_id:
            return False

        url = 'https://api.line.me/v2/bot/message/push'
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        }
        payload = {
            'to': line_user_id,
            'messages': [{'type': 'text', 'text': text_message}],
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code == 200:
                return True
            _logger.error("LINE push failed. status=%s body=%s", response.status_code, response.text)
            return False
        except Exception:
            _logger.exception("Failed to send LINE push")
            return False
