from odoo import fields, models

class ResCompany(models.Model):
    _inherit = 'res.company'

    aacc_line_qr_code = fields.Binary(string="LINE Payment QR Code", copy=False)
    
    slip_verification_provider = fields.Selection(
        [('slipok', 'SlipOK'), ('slip2go', 'Slip2Go')],
        string="Slip Verification Provider",
        default='slipok',
        required=True
    )
    slip2go_api_secret = fields.Char(string="Slip2Go API Secret")
