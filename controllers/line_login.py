import json
import logging
import requests
from urllib.parse import urlencode

from odoo import http, _
from odoo.http import request
from odoo.exceptions import UserError

from werkzeug.exceptions import NotFound

_logger = logging.getLogger(__name__)


class LineLoginController(http.Controller):

    @http.route('/line/payment/qrcode/<int:company_id>', type='http', auth='public', cors='*')
    def line_payment_qrcode(self, company_id, **kwargs):
        """ Serve the payment QR code image for a specific company """
        company = request.env['res.company'].sudo().browse(company_id)
        if company.exists() and company.aacc_line_qr_code:
            import base64
            image_data = base64.b64decode(company.aacc_line_qr_code)
            return request.make_response(image_data, headers=[('Content-Type', 'image/png')])
        raise NotFound()

    @http.route('/line/login', type='http', auth='public', website=True)
    def line_login(self, **kwargs):
        """ Redirect to LINE Authorization URL """
        config = request.env['ir.config_parameter'].sudo()
        client_id = config.get_param('aacc_line.login_channel_id')
        
        if not client_id:
            return request.render('http_routing.404', {'message': _('LINE Login is not configured.')})

        # use web.base.url to support ngrok/devtunnels properly
        base_url = config.get_param('web.base.url', request.httprequest.url_root.rstrip('/'))
        redirect_uri = base_url.rstrip('/') + '/line/login/callback'
        
        # In a real app, generate and verify state to prevent CSRF
        state = 'line_login_state_aacc'
        
        params = {
            'response_type': 'code',
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'state': state,
            'scope': 'profile openid',
        }
        
        auth_url = 'https://access.line.me/oauth2/v2.1/authorize?' + urlencode(params)
        return request.redirect(auth_url, local=False)

    @http.route('/line/login/callback', type='http', auth='public', website=True)
    def line_login_callback(self, code=None, state=None, error=None, error_description=None, **kwargs):
        """ Handle redirect from LINE after user authorizes """
        if error:
            _logger.error(f"LINE Login Error: {error} - {error_description}")
            return request.render('aacc_slipok_invoice_payment.line_login_error_template', {
                'error_msg': _('Login cancelled or failed.')
            })

        if not code:
            return request.render('aacc_slipok_invoice_payment.line_login_error_template', {
                'error_msg': _('No authorization code provided.')
            })

        config = request.env['ir.config_parameter'].sudo()
        client_id = config.get_param('aacc_line.login_channel_id')
        client_secret = config.get_param('aacc_line.login_channel_secret')
        
        base_url = config.get_param('web.base.url', request.httprequest.url_root.rstrip('/'))
        redirect_uri = base_url.rstrip('/') + '/line/login/callback'

        # Exchange code for access token
        token_url = 'https://api.line.me/oauth2/v2.1/token'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        payload = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': client_id,
            'client_secret': client_secret,
        }

        try:
            api_res = requests.post(token_url, data=payload, headers=headers, timeout=10)
            api_res.raise_for_status()
            token_data = api_res.json()
        except Exception as e:
            _logger.exception("Failed to get LINE access token")
            return request.render('aacc_slipok_invoice_payment.line_login_error_template', {
                'error_msg': _('Failed to authenticate with LINE.')
            })

        access_token = token_data.get('access_token')

        # Get User Profile
        profile_url = 'https://api.line.me/v2/profile'
        profile_headers = {'Authorization': f'Bearer {access_token}'}
        try:
            profile_res = requests.get(profile_url, headers=profile_headers, timeout=10)
            profile_res.raise_for_status()
            profile_data = profile_res.json()
        except Exception as e:
            _logger.exception("Failed to get LINE profile")
            return request.render('aacc_slipok_invoice_payment.line_login_error_template', {
                'error_msg': _('Failed to fetch LINE profile.')
            })

        line_user_id = profile_data.get('userId')
        display_name = profile_data.get('displayName')
        picture_url = profile_data.get('pictureUrl')

        # Check if partner already exists
        partner = request.env['res.partner'].sudo().search([('line_user_id', '=', line_user_id)], limit=1)

        if partner:
            return request.render('aacc_slipok_invoice_payment.line_registration_success', {
                'partner': partner
            })
        else:
            # Store data in session to collect phone number
            request.session['line_signup_data'] = {
                'line_user_id': line_user_id,
                'name': display_name,
                'image_url': picture_url,
            }
            return request.redirect('/line/register/phone')

    @http.route('/line/register/phone', type='http', auth='public', methods=['GET', 'POST'], website=True, csrf=False)
    def line_register_phone(self, **post):
        """ Ask the user for their phone number to complete registration """
        line_data = request.session.get('line_signup_data')
        if not line_data:
            # Add logging to see if session is lost
            _logger.warning("line_signup_data not found in session during redirect")
            return request.redirect('/line/login')

        if request.httprequest.method == 'POST':
            phone = post.get('phone')
            if not phone:
                return request.render('aacc_slipok_invoice_payment.line_phone_registration', {
                    'error': _('Please enter your phone number.'),
                    'line_data': line_data,
                })
            
            # Create the Partner
            try:
                # Optionally download the picture
                image_1920 = False
                if line_data.get('image_url'):
                    try:
                        pic_res = requests.get(line_data.get('image_url'), timeout=5)
                        if pic_res.status_code == 200:
                            import base64
                            image_1920 = base64.b64encode(pic_res.content)
                    except Exception:
                        pass # Ignore image download failure

                partner_vals = {
                    'name': line_data.get('name'),
                    'line_user_id': line_data.get('line_user_id'),
                    'phone': phone,
                }
                if image_1920:
                    partner_vals['image_1920'] = image_1920

                partner = request.env['res.partner'].sudo().create(partner_vals)
                
                # Clear session
                request.session.pop('line_signup_data', None)
                
                return request.render('aacc_slipok_invoice_payment.line_registration_success', {
                    'partner': partner
                })

            except Exception as e:
                _logger.exception("Failed to create partner from LINE login")
                return request.render('aacc_slipok_invoice_payment.line_phone_registration', {
                    'error': _('An error occurred while creating your profile. Please try again: ') + str(e),
                    'line_data': line_data,
                })

        return request.render('aacc_slipok_invoice_payment.line_phone_registration', {
            'line_data': line_data,
        })
