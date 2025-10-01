#!/usr/bin/env python3
"""
Quick test script to verify Paystack API connection
"""

import os
from dotenv import load_dotenv
from paystack_integration import PaystackAPI

# Load environment variables
load_dotenv()

PAYSTACK_SECRET_KEY = os.getenv('PAYSTACK_SECRET_KEY', '')
PAYSTACK_PUBLIC_KEY = os.getenv('PAYSTACK_PUBLIC_KEY', '')

print("=" * 60)
print("PAYSTACK CONNECTION TEST")
print("=" * 60)
print(f"Secret Key: {PAYSTACK_SECRET_KEY[:15]}...{PAYSTACK_SECRET_KEY[-10:] if len(PAYSTACK_SECRET_KEY) > 25 else ''}")
print(f"Public Key: {PAYSTACK_PUBLIC_KEY[:15]}...{PAYSTACK_PUBLIC_KEY[-10:] if len(PAYSTACK_PUBLIC_KEY) > 25 else ''}")
print("=" * 60)

# Initialize Paystack
paystack = PaystackAPI(
    secret_key=PAYSTACK_SECRET_KEY,
    public_key=PAYSTACK_PUBLIC_KEY,
    env='test'
)

print("\n1. Testing API connection...")
print("-" * 60)

# Test with minimal data
result = paystack.initialize_transaction(
    email="test@example.com",
    amount=100,  # KES 100
    reference=f"TEST-{int(os.urandom(4).hex(), 16)}",
    callback_url="http://localhost:5000/test-callback"
)

if result['success']:
    print("✅ SUCCESS! Paystack API is working!")
    print(f"Authorization URL: {result['authorization_url']}")
    print(f"Reference: {result['reference']}")
else:
    print("❌ FAILED!")
    print(f"Error: {result.get('error')}")

print("\n2. Testing bank list...")
print("-" * 60)

banks = paystack.get_banks(country='kenya')
if banks['success']:
    print(f"✅ Found {len(banks['banks'])} banks in Kenya")
    print("First 5 banks:")
    for bank in banks['banks'][:5]:
        print(f"  - {bank['name']} ({bank['code']})")
else:
    print(f"❌ Failed to fetch banks: {banks.get('error')}")

print("\n" + "=" * 60)
print("TEST COMPLETE")
print("=" * 60)
