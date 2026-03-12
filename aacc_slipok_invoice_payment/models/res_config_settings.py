from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    aacc_slipok_invoice_line_channel_secret = fields.Char(
        string="LINE Channel Secret",
        config_parameter='aacc_line.channel_secret',
    )
    aacc_slipok_invoice_line_access_token = fields.Char(
        string="LINE Access Token",
        config_parameter='aacc_line.access_token',
    )
    aacc_line_qr_code = fields.Binary(
        related='company_id.aacc_line_qr_code',
        readonly=False,
    )
    aacc_line_login_channel_id = fields.Char(
        string="LINE Login Channel ID",
        config_parameter='aacc_line.login_channel_id',
    )
    aacc_line_login_channel_secret = fields.Char(
        string="LINE Login Channel Secret",
        config_parameter='aacc_line.login_channel_secret',
    )
    slip_verification_provider = fields.Selection(
        related='company_id.slip_verification_provider',
        readonly=False,
    )
    slip2go_api_secret = fields.Char(
        related='company_id.slip2go_api_secret',
        readonly=False,
    )
    aacc_slipok_invoice_branch_id = fields.Char(
        string="SlipOK Branch ID",
        config_parameter='aacc_slipok_invoice.branch_id',
    )
    aacc_slipok_invoice_api_key = fields.Char(
        string="SlipOK API Key",
        config_parameter='aacc_slipok_invoice.api_key',
    )
    aacc_slipok_invoice_company_bank_account = fields.Char(
        string="Company Bank Account",
        config_parameter='aacc_slipok_invoice.company_bank_account',
        help="Destination bank account/PromptPay for slip verification (can be multiple separated by comma)",
    )
    aacc_slipok_invoice_payment_journal_id = fields.Many2one(
        'account.journal',
        string="Default Payment Journal",
        domain="[('type', 'in', ['bank', 'cash'])]",
        config_parameter='aacc_slipok_invoice.payment_journal_id',
        check_company=True,
    )
