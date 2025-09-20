"""
Cryptographic utilities for securing sensitive data like M-Pesa credentials
"""
import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class CredentialEncryption:
    """Handle encryption/decryption of sensitive credentials"""

    def __init__(self, secret_key=None):
        """Initialize encryption with a secret key"""
        if secret_key is None:
            secret_key = os.environ.get('ENCRYPTION_SECRET_KEY')
            if not secret_key:
                # Generate a key if none provided (for development)
                secret_key = base64.urlsafe_b64encode(os.urandom(32))

        if isinstance(secret_key, str):
            secret_key = secret_key.encode()

        # Derive a Fernet key from the secret
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b'water_billing_salt',  # In production, use random salt per admin
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(secret_key))
        self.cipher_suite = Fernet(key)

    def encrypt(self, plaintext):
        """Encrypt sensitive data"""
        if not plaintext:
            return None

        if isinstance(plaintext, str):
            plaintext = plaintext.encode('utf-8')

        encrypted_data = self.cipher_suite.encrypt(plaintext)
        return base64.urlsafe_b64encode(encrypted_data).decode('utf-8')

    def decrypt(self, encrypted_data):
        """Decrypt sensitive data"""
        if not encrypted_data:
            return None

        try:
            decoded_data = base64.urlsafe_b64decode(encrypted_data.encode('utf-8'))
            decrypted_data = self.cipher_suite.decrypt(decoded_data)
            return decrypted_data.decode('utf-8')
        except Exception as e:
            print(f"Decryption error: {e}")
            return None

    def encrypt_mpesa_credentials(self, credentials):
        """Encrypt M-Pesa credentials dictionary"""
        encrypted_creds = {}

        sensitive_fields = [
            'consumer_key', 'consumer_secret', 'passkey',
            'initiator_password', 'security_credential'
        ]

        for key, value in credentials.items():
            if key in sensitive_fields and value:
                encrypted_creds[key] = self.encrypt(value)
            else:
                encrypted_creds[key] = value

        return encrypted_creds

    def decrypt_mpesa_credentials(self, encrypted_credentials):
        """Decrypt M-Pesa credentials dictionary"""
        decrypted_creds = {}

        sensitive_fields = [
            'consumer_key', 'consumer_secret', 'passkey',
            'initiator_password', 'security_credential'
        ]

        for key, value in encrypted_credentials.items():
            if key in sensitive_fields and value:
                decrypted_creds[key] = self.decrypt(value)
            else:
                decrypted_creds[key] = value

        return decrypted_creds


def get_encryption_instance():
    """Get a singleton encryption instance"""
    if not hasattr(get_encryption_instance, '_instance'):
        get_encryption_instance._instance = CredentialEncryption()
    return get_encryption_instance._instance


# Convenience functions
def encrypt_data(plaintext):
    """Encrypt data using the singleton instance"""
    return get_encryption_instance().encrypt(plaintext)


def decrypt_data(encrypted_data):
    """Decrypt data using the singleton instance"""
    return get_encryption_instance().decrypt(encrypted_data)


def encrypt_mpesa_credentials(credentials):
    """Encrypt M-Pesa credentials"""
    return get_encryption_instance().encrypt_mpesa_credentials(credentials)


def decrypt_mpesa_credentials(encrypted_credentials):
    """Decrypt M-Pesa credentials"""
    return get_encryption_instance().decrypt_mpesa_credentials(encrypted_credentials)