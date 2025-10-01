# paystack_integration.py
import requests
import hashlib
import hmac
import logging
from datetime import datetime

class PaystackAPI:
    """
    Paystack Payment Integration for Kenya
    Supports M-Pesa, Cards, and Bank Transfers
    """

    def __init__(self, secret_key, public_key=None, env='test'):
        """
        Initialize Paystack API

        Args:
            secret_key: Paystack secret key
            public_key: Paystack public key (optional, for frontend)
            env: 'test' or 'live'
        """
        self.secret_key = secret_key
        self.public_key = public_key
        self.env = env
        self.base_url = 'https://api.paystack.co'
        self.logger = logging.getLogger(__name__)

    def _get_headers(self):
        """Get authorization headers for API requests"""
        return {
            'Authorization': f'Bearer {self.secret_key}',
            'Content-Type': 'application/json'
        }

    def initialize_transaction(self, email, amount, reference,
                               callback_url=None, metadata=None,
                               channels=None):
        """
        Initialize a payment transaction

        Args:
            email: Customer email
            amount: Amount in KES (will be converted to kobo)
            reference: Unique transaction reference
            callback_url: URL to redirect after payment
            metadata: Additional transaction data (dict)
            channels: Payment channels ['mobile_money', 'card', 'bank']

        Returns:
            dict: Response with authorization_url and access_code
        """
        url = f"{self.base_url}/transaction/initialize"

        # Default to mobile money (M-Pesa) if no channels specified
        if channels is None:
            channels = ['mobile_money', 'card']

        payload = {
            "email": email,
            "amount": int(amount * 100),  # Convert to kobo (1 KES = 100 kobo)
            "reference": reference,
            "currency": "KES",
            "channels": channels
        }

        if callback_url:
            payload["callback_url"] = callback_url

        if metadata:
            payload["metadata"] = metadata

        try:
            response = requests.post(url, json=payload, headers=self._get_headers())
            response.raise_for_status()
            result = response.json()

            if result.get('status'):
                self.logger.info(f"Paystack transaction initialized: {reference}")
                return {
                    'success': True,
                    'authorization_url': result['data']['authorization_url'],
                    'access_code': result['data']['access_code'],
                    'reference': result['data']['reference']
                }
            else:
                self.logger.error(f"Paystack initialization failed: {result.get('message')}")
                return {
                    'success': False,
                    'error': result.get('message', 'Transaction initialization failed')
                }

        except requests.exceptions.HTTPError as e:
            # Log the full error response from Paystack
            error_detail = "Unknown error"
            try:
                error_response = e.response.json()
                error_detail = error_response.get('message', str(e))
                self.logger.error(f"Paystack API error: {error_detail}")
                self.logger.error(f"Payload sent: {payload}")
            except:
                self.logger.error(f"Paystack API error: {str(e)}")

            return {
                'success': False,
                'error': f'Payment initialization failed: {error_detail}'
            }
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Paystack network error: {str(e)}")
            return {
                'success': False,
                'error': f'Network error: {str(e)}'
            }

    def verify_transaction(self, reference):
        """
        Verify a transaction

        Args:
            reference: Transaction reference to verify

        Returns:
            dict: Transaction details including status and amount
        """
        url = f"{self.base_url}/transaction/verify/{reference}"

        try:
            response = requests.get(url, headers=self._get_headers())
            response.raise_for_status()
            result = response.json()

            if result.get('status') and result.get('data'):
                data = result['data']
                return {
                    'success': True,
                    'status': data.get('status'),  # 'success', 'failed', 'abandoned'
                    'amount': data.get('amount') / 100,  # Convert from kobo to KES
                    'reference': data.get('reference'),
                    'customer': data.get('customer'),
                    'channel': data.get('channel'),  # 'mobile_money', 'card', etc.
                    'paid_at': data.get('paid_at'),
                    'metadata': data.get('metadata', {}),
                    'fees': data.get('fees') / 100 if data.get('fees') else 0,
                    'authorization': data.get('authorization')
                }
            else:
                return {
                    'success': False,
                    'error': result.get('message', 'Verification failed')
                }

        except requests.exceptions.RequestException as e:
            self.logger.error(f"Paystack verification error: {str(e)}")
            return {
                'success': False,
                'error': f'Verification error: {str(e)}'
            }

    def verify_webhook_signature(self, payload, signature):
        """
        Verify webhook signature from Paystack

        Args:
            payload: Raw request body (bytes)
            signature: X-Paystack-Signature header value

        Returns:
            bool: True if signature is valid
        """
        computed_signature = hmac.new(
            self.secret_key.encode('utf-8'),
            payload,
            hashlib.sha512
        ).hexdigest()

        return hmac.compare_digest(computed_signature, signature)

    def list_transactions(self, per_page=50, page=1, status=None,
                         customer=None, from_date=None, to_date=None):
        """
        List transactions

        Args:
            per_page: Number of results per page
            page: Page number
            status: Filter by status ('success', 'failed', 'abandoned')
            customer: Filter by customer ID
            from_date: Start date (datetime object)
            to_date: End date (datetime object)

        Returns:
            dict: List of transactions
        """
        url = f"{self.base_url}/transaction"

        params = {
            'perPage': per_page,
            'page': page
        }

        if status:
            params['status'] = status
        if customer:
            params['customer'] = customer
        if from_date:
            params['from'] = from_date.strftime('%Y-%m-%d')
        if to_date:
            params['to'] = to_date.strftime('%Y-%m-%d')

        try:
            response = requests.get(url, params=params, headers=self._get_headers())
            response.raise_for_status()
            result = response.json()

            if result.get('status'):
                return {
                    'success': True,
                    'transactions': result['data'],
                    'meta': result.get('meta', {})
                }
            else:
                return {
                    'success': False,
                    'error': result.get('message', 'Failed to fetch transactions')
                }

        except requests.exceptions.RequestException as e:
            self.logger.error(f"Paystack list transactions error: {str(e)}")
            return {
                'success': False,
                'error': f'Error fetching transactions: {str(e)}'
            }

    def create_transfer_recipient(self, account_name, account_number, bank_code):
        """
        Create a transfer recipient (for sending money out)

        Args:
            account_name: Recipient account name
            account_number: Recipient account number
            bank_code: Bank code (e.g., '063' for Diamond Trust Bank Kenya)

        Returns:
            dict: Recipient details with recipient_code
        """
        url = f"{self.base_url}/transferrecipient"

        payload = {
            "type": "nuban",  # For Nigerian banks, use 'mobile_money' for M-Pesa
            "name": account_name,
            "account_number": account_number,
            "bank_code": bank_code,
            "currency": "KES"
        }

        try:
            response = requests.post(url, json=payload, headers=self._get_headers())
            response.raise_for_status()
            result = response.json()

            if result.get('status'):
                return {
                    'success': True,
                    'recipient_code': result['data']['recipient_code'],
                    'details': result['data']
                }
            else:
                return {
                    'success': False,
                    'error': result.get('message', 'Failed to create recipient')
                }

        except requests.exceptions.RequestException as e:
            self.logger.error(f"Paystack create recipient error: {str(e)}")
            return {
                'success': False,
                'error': f'Error creating recipient: {str(e)}'
            }

    def initiate_transfer(self, recipient_code, amount, reason=None, reference=None):
        """
        Initiate a transfer to a recipient

        Args:
            recipient_code: Recipient code from create_transfer_recipient
            amount: Amount in KES
            reason: Transfer reason/description
            reference: Unique transfer reference

        Returns:
            dict: Transfer details
        """
        url = f"{self.base_url}/transfer"

        if reference is None:
            reference = f"TRF-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        payload = {
            "source": "balance",
            "amount": int(amount * 100),  # Convert to kobo
            "recipient": recipient_code,
            "reason": reason or "Payment transfer",
            "reference": reference,
            "currency": "KES"
        }

        try:
            response = requests.post(url, json=payload, headers=self._get_headers())
            response.raise_for_status()
            result = response.json()

            if result.get('status'):
                return {
                    'success': True,
                    'transfer_code': result['data']['transfer_code'],
                    'reference': result['data']['reference'],
                    'status': result['data']['status']
                }
            else:
                return {
                    'success': False,
                    'error': result.get('message', 'Transfer failed')
                }

        except requests.exceptions.RequestException as e:
            self.logger.error(f"Paystack transfer error: {str(e)}")
            return {
                'success': False,
                'error': f'Transfer error: {str(e)}'
            }

    def get_banks(self, country='kenya'):
        """
        Get list of banks supported by Paystack

        Args:
            country: Country code ('kenya', 'nigeria', 'ghana', 'south-africa')

        Returns:
            dict: List of banks
        """
        url = f"{self.base_url}/bank"
        params = {'country': country}

        try:
            response = requests.get(url, params=params, headers=self._get_headers())
            response.raise_for_status()
            result = response.json()

            if result.get('status'):
                return {
                    'success': True,
                    'banks': result['data']
                }
            else:
                return {
                    'success': False,
                    'error': result.get('message', 'Failed to fetch banks')
                }

        except requests.exceptions.RequestException as e:
            self.logger.error(f"Paystack get banks error: {str(e)}")
            return {
                'success': False,
                'error': f'Error fetching banks: {str(e)}'
            }
