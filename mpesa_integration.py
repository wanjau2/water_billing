# mpesa_integration.py
import requests
import base64
from datetime import datetime
import json
from flask import current_app

class MpesaAPI:
    def __init__(self, consumer_key, consumer_secret, shortcode, passkey, env='sandbox'):
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.shortcode = shortcode
        self.passkey = passkey
        self.env = env
        
        if env == 'sandbox':
            self.base_url = 'https://sandbox.safaricom.co.ke'
        else:
            self.base_url = 'https://api.safaricom.co.ke'
            
    def get_access_token(self):
        """Get OAuth access token from M-Pesa"""
        url = f"{self.base_url}/oauth/v1/generate?grant_type=client_credentials"
        
        # Create base64 encoded string
        keys = f"{self.consumer_key}:{self.consumer_secret}"
        keys_encoded = base64.b64encode(keys.encode()).decode('utf-8')
        
        headers = {
            'Authorization': f'Basic {keys_encoded}'
        }
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()['access_token']
        except Exception as e:
            current_app.logger.error(f"Error getting M-Pesa token: {e}")
            return None
            
    def stk_push(self, phone_number, amount, account_reference, callback_url):
        """Initiate STK push for payment"""
        access_token = self.get_access_token()
        if not access_token:
            return {'error': 'Failed to get access token'}
            
        url = f"{self.base_url}/mpesa/stkpush/v1/processrequest"
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Format phone number (remove + if present)
        if phone_number.startswith('+'):
            phone_number = phone_number[1:]
            
        # Generate timestamp
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        
        # Generate password
        data_to_encode = f"{self.shortcode}{self.passkey}{timestamp}"
        password = base64.b64encode(data_to_encode.encode()).decode('utf-8')
        
        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": int(amount),
            "PartyA": phone_number,
            "PartyB": self.shortcode,
            "PhoneNumber": phone_number,
            "CallBackURL": callback_url,
            "AccountReference": account_reference,
            "TransactionDesc": f"Subscription payment for {account_reference}"
        }
        
        try:
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            current_app.logger.error(f"STK push error: {e}")
            return {'error': str(e)}