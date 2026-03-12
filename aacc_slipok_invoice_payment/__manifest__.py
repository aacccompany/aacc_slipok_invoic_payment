{
    'name': 'LINE Payment & Slip Auto-Verify Integration',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Automate invoice payments via LINE with Slip Verification, Dynamic QR Codes, and Auto-Reconciliation',
    'description': """
LINE Payment & Slip Auto-Verify Integration
=============================================
Transform your customer's payment experience with seamless LINE integration and automated slip verification.

Key Features:
-------------
* **Send Bill via LINE**: Instantly push invoice details directly to your customer's LINE chat.
* **Dynamic Payment QR Code**: Send a scannable PromptPay QR Code with the exact invoice amount embedded.
* **LINE Login Integration**: Allow customers to link their LINE accounts to Odoo seamlessly.
* **Automated Slip Verification**: Customers upload their bank slips to the chat, and the AI verifies the exact amount, transaction ID, and receiver bank automatically.
* **Auto-Reconciliation**: Upon successful slip verification, the system automatically registers the payment in Odoo and marks the invoice as Paid in real-time.
* **Multi-Account Support**: Supports checking against multiple company bank accounts/PromptPay IDs automatically.
* **Fraud Prevention**: Detects duplicate slips and mismatched amounts instantly, notifying the customer on LINE automatically.

Suitable for any business providing direct sales or services and accepting bank transfers in Thailand!
    """,
    'author': 'AACC',
    'website': 'https://www.yourcompany.com',
    'support': 'support@yourcompany.com',
    'license': 'OPL-1',
    'price': 27.07,
    'currency': 'USD',
    'depends': ['account', 'mail', 'base', 'website'],
    'data': [
        'views/res_config_settings_views.xml',
        'views/res_partner_views.xml',
        'views/line_login_templates.xml',
        'views/account_move_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
}
