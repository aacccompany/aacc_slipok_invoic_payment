import json
import logging

import requests

_logger = logging.getLogger(__name__)


def _dig(data, *path):
    current = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _collect_strings(node):
    values = []
    if isinstance(node, str):
        values.append(node)
    elif isinstance(node, dict):
        for item in node.values():
            values.extend(_collect_strings(item))
    elif isinstance(node, list):
        for item in node:
            values.extend(_collect_strings(item))
    return values


def _extract_receiver_account(data):
    candidates = [
        _dig(data, 'receiver', 'account', 'bank', 'account'),
        _dig(data, 'receiver', 'account', 'value'),
        _dig(data, 'receiver', 'account', 'account'),
        _dig(data, 'receiver', 'bank', 'account'),
        _dig(data, 'receiver', 'proxy', 'value'),
        _dig(data, 'receiver', 'promptpay', 'value'),
        _dig(data, 'receiver', 'id'),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    receiver_strings = _collect_strings(data.get('receiver', {}))
    if receiver_strings:
        # Prefer the value that contains the most digits (masked accounts still keep trailing digits).
        return max(receiver_strings, key=lambda s: sum(ch.isdigit() for ch in s))
    return ''


def verify_slip_api(branch_id, api_key, image_bytes):
    """Send slip image bytes to SlipOK and normalize response."""
    url = f"https://api.slipok.com/api/line/apikey/{branch_id}"
    headers = {'x-authorization': api_key}
    files = {'files': ('slip.jpg', image_bytes, 'image/jpeg')}

    try:
        response = requests.post(url, headers=headers, files=files, timeout=20)
        try:
            response_data = response.json()
        except ValueError:
            response_data = {}

        if response.status_code == 200 and response_data.get('success'):
            data = response_data.get('data', {})
            return {
                'success': True,
                'amount': data.get('amount'),
                'transaction_id': data.get('transRef'),
                'receiver_account': _extract_receiver_account(data),
                'raw_response': json.dumps(response_data),
            }

        message = response_data.get('message') or response.text or 'Slip API verification failed'
        return {
            'success': False,
            'error_msg': message,
            'raw_response': json.dumps(response_data or {'http_status': response.status_code}),
        }
    except Exception as exc:
        _logger.exception("SlipOK API call failed")
        return {
            'success': False,
            'error_msg': str(exc),
            'raw_response': '{}',
        }

def _extract_qr_payload(image_bytes):
    """Attempt to decode QR payload using pyzbar or cv2 if available."""
    try:
        from pyzbar.pyzbar import decode
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_bytes))
        decoded = decode(img)
        if decoded:
            return decoded[0].data.decode('utf-8')
    except ImportError:
        pass

    try:
        import cv2
        import numpy as np
        np_arr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if img is not None:
            detector = cv2.QRCodeDetector()
            val, pts, qr_code = detector.detectAndDecode(img)
            if val:
                return val
    except ImportError:
        pass
    
    return None


def verify_slip2go_api(api_secret, qr_payload):
    """Send QR payload to Slip2Go and normalize response."""
    url = "https://connect.slip2go.com/api/verify-slip/qr-code/info"
    headers = {
        'Authorization': f'Bearer {api_secret}',
        'Content-Type': 'application/json'
    }
    payload = {"payload": {"qrCode": qr_payload}}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        try:
            response_data = response.json()
        except ValueError:
            response_data = {}

        if response.status_code == 200 and str(response_data.get('code')) == '200000':
            data = response_data.get('data', {})
            return {
                'success': True,
                'amount': data.get('amount'),
                'transaction_id': data.get('transRef'),
                'receiver_account': _extract_receiver_account(data) or data.get('receiverAccount') or data.get('receiverProxyId'),
                'raw_response': json.dumps(response_data),
            }

        message = response_data.get('message') or response.text or 'Slip2Go verification failed'
        return {
            'success': False,
            'error_msg': message,
            'raw_response': json.dumps(response_data or {'http_status': response.status_code}),
        }
    except Exception as exc:
        _logger.exception("Slip2Go API call failed")
        return {
            'success': False,
            'error_msg': str(exc),
            'raw_response': '{}',
        }
