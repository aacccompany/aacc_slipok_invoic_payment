import base64
import logging

import requests

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare

from ..tools.slip_verify_api import verify_slip_api

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    slip_image = fields.Binary(string="Slip Image", attachment=True, copy=False)
    slip_image_filename = fields.Char(string="Slip Filename", copy=False)
    slip_state = fields.Selection(
        [
            ('none', 'No Slip'),
            ('verified', 'Verified'),
            ('rejected', 'Rejected'),
        ],
        string="Slip Verification State",
        default='none',
        copy=False,
        tracking=True,
    )
    slip_message = fields.Char(string="Verification Message", copy=False, readonly=True)
    slip_transaction_id = fields.Char(string="Slip Transaction ID", copy=False, readonly=True, index=True)
    slip_verified_amount = fields.Monetary(string="Slip Amount", currency_field='currency_id', copy=False, readonly=True)
    slip_receiver_account = fields.Char(string="Receiver Account", copy=False, readonly=True)
    slip_verified_at = fields.Datetime(string="Verified At", copy=False, readonly=True)
    slip_response = fields.Text(string="Verification Response", copy=False, readonly=True)

    line_user_id = fields.Char(related='partner_id.line_user_id', readonly=False, store=True, string="LINE User ID", copy=False)
    is_active_line_bill = fields.Boolean(string="Active LINE Bill", default=False, copy=False)
    line_payment_state = fields.Selection(
        [
            ('waiting_payment', 'Waiting Payment'),
            ('slip_received', 'Slip Received'),
            ('verifying', 'Verifying'),
            ('verified', 'Verified'),
            ('rejected', 'Rejected'),
        ],
        string="LINE Payment State",
        copy=False,
    )

    def action_send_line_invoice_bill(self):
        for move in self:
            move._action_send_line_invoice_bill()
        return True

    def _action_send_line_invoice_bill(self):
        self.ensure_one()

        if self.move_type != 'out_invoice':
            raise UserError(_("Send Bill via LINE supports only Customer Invoices."))
        if self.state != 'posted':
            raise UserError(_("Please post the invoice before sending to LINE."))
        if self.payment_state in ('paid', 'in_payment'):
            raise UserError(_("Invoice already paid or in payment flow."))
        if not self.line_user_id:
            raise UserError(_("Please set LINE User ID before sending bill."))

        access_token = self.env['ir.config_parameter'].sudo().get_param('aacc_line.access_token')
        if not access_token:
            raise UserError(_("LINE Access Token is not configured."))

        old_invoices = self.search([
            ('id', '!=', self.id),
            ('move_type', '=', 'out_invoice'),
            ('line_user_id', '=', self.line_user_id),
            ('is_active_line_bill', '=', True),
            ('state', '=', 'posted'),
        ])
        old_invoices.write({'is_active_line_bill': False})

        self.write({
            'is_active_line_bill': True,
            'line_payment_state': 'waiting_payment',
        })

        items_list = []
        for line in self.invoice_line_ids.filtered(lambda l: l.display_type == 'product' or not l.display_type):
            name = line.name or line.product_id.name or 'Item'
            if len(name) > 20:
                name = name[:17] + '...'
            
            # Format: '• Item Name           100.00'
            price_str = f"{line.price_total:,.2f}"
            padded_name = f"• {name}".ljust(25, ' ')
            items_list.append(f"{padded_name} {price_str}")
        
        items_text = "\n".join(items_list) if items_list else "• No items found"

        message_text = _(
            "Hello %(partner_name)s,\n\n"
            "Here is your invoice: %(invoice)s\n"
            "Items:\n%(items)s\n\n"
            "Total Amount: %(amount).2f %(currency)s\n\n"
            "Please complete your payment. "
            "After paying, simply upload your payment slip right here in this chat for automatic verification. 🚀\n\n"
            "Thank you for your business! 🙏"
        ) % {
            'partner_name': self.partner_id.name or 'Customer',
            'invoice': self.name,
            'items': items_text,
            'amount': self.amount_residual,
            'currency': self.currency_id.name,
        }
        
        messages = [{'type': 'text', 'text': message_text}]
        company = self.company_id or self.env.company
        if company.aacc_line_qr_code:
            base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
            qr_url = f"{base_url}/line/payment/qrcode/{company.id}"
            messages.append({
                'type': 'image',
                'originalContentUrl': qr_url,
                'previewImageUrl': qr_url,
            })

        self._send_line_push_message(access_token, self.line_user_id, messages)

    def _send_line_push_message(self, token, line_user_id, messages_payload):
        url = 'https://api.line.me/v2/bot/message/push'
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        }
        payload = {
            'to': line_user_id,
            'messages': messages_payload,
        }
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            _logger.error("Failed to push LINE message: %s", exc)
            raise UserError(_("Failed to send LINE message. Please check configuration/logs.")) from exc

    def action_verify_slip_and_register_payment(self):
        for move in self:
            move._action_verify_slip_and_register_payment()
        return True

    def _action_verify_slip_and_register_payment(self):
        self.ensure_one()

        if self.move_type != 'out_invoice':
            raise UserError(_("Slip verification supports only Customer Invoices."))
        if self.state != 'posted':
            raise UserError(_("Please post the invoice before verifying slip."))
        if self.payment_state in ('in_payment', 'paid'):
            raise UserError(_("This invoice is already in payment flow or fully paid."))
        if not self.slip_image:
            raise UserError(_("Please upload slip image first."))

        provider, branch_id, api_key, slip2go_secret, company_bank, journal = self._slip_get_configuration()

        try:
            image_bytes = base64.b64decode(self.slip_image)
        except Exception as exc:
            _logger.error("Invalid base64 slip image for invoice %s: %s", self.name, exc)
            raise UserError(_("Uploaded slip image is invalid.")) from exc

        if provider == 'slip2go':
            from ..tools.slip_verify_api import verify_slip2go_api, _extract_qr_payload
            qr_payload = _extract_qr_payload(image_bytes)
            if not qr_payload:
                reason = "Could not extract QR code payload from image for Slip2Go verification."
                self._mark_slip_rejected(reason)
                raise UserError(_(reason))
            result = verify_slip2go_api(slip2go_secret, qr_payload)
        else:
            result = verify_slip_api(branch_id, api_key, image_bytes)

        self.slip_response = result.get('raw_response') or '{}'

        if not result.get('success'):
            reason = result.get('error_msg') or _('Slip verification failed')
            self._mark_slip_rejected(reason)
            raise UserError(_("Slip verification failed: %s") % reason)

        amount = float(result.get('amount') or 0.0)
        expected_amount = self.amount_residual
        if float_compare(amount, expected_amount, precision_rounding=self.currency_id.rounding) != 0:
            reason = _("Amount mismatch. Invoice residual: %(expected).2f, slip amount: %(actual).2f") % {
                'expected': expected_amount,
                'actual': amount,
            }
            self._mark_slip_rejected(reason)
            raise UserError(reason)

        receiver_account = result.get('receiver_account') or ''
        receiver_digits = self._normalize_account_number(receiver_account)
        expected_accounts = self._split_expected_accounts(company_bank)

        if expected_accounts and receiver_digits:
            is_match = any(
                self._is_receiver_account_match(expected_account, receiver_account)
                for expected_account in expected_accounts
            )
            if not is_match:
                expected_last4 = ', '.join(
                    (self._normalize_account_number(item)[-4:] or '-')
                    for item in expected_accounts
                )
                received_last4 = receiver_digits[-4:] if receiver_digits else '-'
                reason = _(
                    "Receiver account mismatch with configured company account. "
                    "Expected last 4: %(expected)s, received last 4: %(received)s"
                ) % {
                    'expected': expected_last4,
                    'received': received_last4,
                }
                self._mark_slip_rejected(reason)
                raise UserError(reason)

        if expected_accounts and not receiver_digits:
            self.message_post(
                body=_(
                    "Verification API did not return receiver account digits, so receiver account validation was skipped."
                )
            )

        transaction_id = result.get('transaction_id')
        if not transaction_id:
            reason = _("Verification API did not return transaction ID.")
            self._mark_slip_rejected(reason)
            raise UserError(reason)

        duplicate_invoice = self.search([
            ('id', '!=', self.id),
            ('slip_transaction_id', '=', transaction_id),
        ], limit=1)
        if duplicate_invoice:
            reason = _("Duplicate slip transaction detected: %(tx)s (already used by %(invoice)s)") % {
                'tx': transaction_id,
                'invoice': duplicate_invoice.name,
            }
            self._mark_slip_rejected(reason)
            raise UserError(reason)

        self._slip_register_payment(journal=journal, amount=amount, transaction_id=transaction_id)

        success_message = _("Slip verified and payment registered successfully.")
        self.write({
            'slip_state': 'verified',
            'slip_message': success_message,
            'slip_verified_amount': amount,
            'slip_receiver_account': receiver_account,
            'slip_transaction_id': transaction_id,
            'slip_verified_at': fields.Datetime.now(),
            'line_payment_state': 'verified',
            'is_active_line_bill': False,
        })
        self.message_post(body=success_message)

    def _slip_get_configuration(self):
        config = self.env['ir.config_parameter'].sudo()
        provider = self.company_id.slip_verification_provider or 'slipok'

        branch_id = config.get_param('aacc_slipok_invoice.branch_id') or config.get_param('aacc_line.slipok_branch_id')
        api_key = config.get_param('aacc_slipok_invoice.api_key') or config.get_param('aacc_line.slipok_api_key')
        slip2go_secret = self.company_id.slip2go_api_secret or ''
        configured_company_bank = (
            config.get_param('aacc_slipok_invoice.company_bank_account')
            or config.get_param('aacc_line.company_bank_account')
            or ''
        )

        # Combine values from module settings + company bank accounts for practical deployment.
        expected_accounts = []
        expected_accounts.extend(self._split_expected_accounts(configured_company_bank))
        for bank in self.company_id.partner_id.bank_ids:
            expected_accounts.extend(self._split_expected_accounts(bank.acc_number))
        company_bank = ','.join(dict.fromkeys(expected_accounts))

        if provider == 'slipok' and (not branch_id or not api_key):
            raise UserError(_("SlipOK Branch ID / API Key is not configured."))
        elif provider == 'slip2go' and not slip2go_secret:
            raise UserError(_("Slip2Go API Secret is not configured."))

        journal = False
        journal_id = config.get_param('aacc_slipok_invoice.payment_journal_id')
        if journal_id and str(journal_id).isdigit():
            journal = self.env['account.journal'].sudo().browse(int(journal_id)).exists()

        if not journal:
            journal = self.env['account.journal'].sudo().search([
                ('company_id', '=', self.company_id.id),
                ('type', 'in', ('bank', 'cash')),
            ], limit=1)

        if not journal:
            raise UserError(_("No Bank/Cash journal found for automatic payment registration."))

        return provider, branch_id, api_key, slip2go_secret, company_bank, journal

    def _slip_register_payment(self, journal, amount, transaction_id):
        self.ensure_one()
        register_ctx = {
            'active_model': 'account.move',
            'active_ids': self.ids,
        }
        register_model = self.env['account.payment.register'].with_context(**register_ctx)

        vals = {
            'journal_id': journal.id,
            'amount': amount,
            'payment_date': fields.Date.context_today(self),
        }

        payment_method_line = journal.inbound_payment_method_line_ids[:1]
        if payment_method_line and 'payment_method_line_id' in register_model._fields:
            vals['payment_method_line_id'] = payment_method_line.id

        if 'communication' in register_model._fields:
            vals['communication'] = transaction_id
        if 'payment_reference' in register_model._fields:
            vals['payment_reference'] = transaction_id

        register = register_model.create(vals)
        register.action_create_payments()

    def _mark_slip_rejected(self, reason):
        self.write({
            'slip_state': 'rejected',
            'slip_message': reason,
            'slip_verified_at': False,
            'line_payment_state': 'rejected',
        })
        self.message_post(body=_("Slip rejected: %s") % reason)

    def _normalize_account_number(self, value):
        return ''.join(ch for ch in str(value or '') if ch.isdigit())

    def _split_expected_accounts(self, value):
        raw = str(value or '')
        parts = []
        for chunk in raw.replace('\n', ',').replace(';', ',').split(','):
            token = chunk.strip()
            if token:
                parts.append(token)
        return parts

    def _is_receiver_account_match(self, configured_account, receiver_account):
        """
        Accept both full account numbers and masked account values from API
        (e.g. XXX-X-XX729-4 or XXX XXX 5723) by matching visible trailing digits.
        """
        expected_digits = self._normalize_account_number(configured_account)
        receiver_digits = self._normalize_account_number(receiver_account)

        if not expected_digits:
            return True
        if not receiver_digits:
            return False

        # Full number match (different separators/spaces are normalized out)
        if expected_digits == receiver_digits:
            return True

        # Masked responses: allow trailing visible digits match (min 4 digits)
        if len(receiver_digits) >= 4 and expected_digits.endswith(receiver_digits):
            return True
        if len(expected_digits) >= 4 and receiver_digits.endswith(expected_digits):
            return True

        return False
