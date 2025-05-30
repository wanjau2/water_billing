# subscription_config.py
SUBSCRIPTION_TIERS = {
    'starter': {
        'name': 'Starter',
        'max_tenants': 5,
        'max_houses': 10,  # Assuming a limit of 10 houses for starter tier
        'features': [
            'Up to 5 tenants',
            'Basic water billing',
            'SMS notifications',
            'Basic reports'
        ],
        'monthly_price': 80,
        'lifetime_price': 1500
    },
    'basic': {
        'name': 'Basic',
        'max_tenants': 20,
        'max_houses': -1,
        'features': [
            'Up to 20 tenants',
            'Water & rent billing',
            'SMS notifications',
            'Excel import/export',
            'Payment tracking'
        ],
        'monthly_price': 400,
        'lifetime_price': 15000  # 10 months equivalent
    },
    'pro': {
        'name': 'Pro',
        'max_tenants': 100,
        'max_houses': -1,
        'features': [
            'Up to 100 tenants',
            'All Basic features',
            'Advanced reporting',
            'Bulk operations',
            'Payment reminders'
        ],
        'monthly_price': 1000,
        'lifetime_price': 35000  # 10 months equivalent
    },
    'business': {
        'name': 'business',
        'max_tenants': 250,
        'max_houses': -1,

        'features': [
            'Up to 250 tenants',
            'All Pro features',
            'Priority support',
            'API access (coming soon)'
        ],
        'monthly_price': 2500,
        'lifetime_price': 55000  # 10 months equivalent
    },
    'enterprise': {
        'name': 'Enterprise',
        'max_tenants': -1,  # Unlimited
        'max_houses': -1,
        'features': [
            'Unlimited tenants',
            'All Enterprise features',
            'Dedicated support',
            'Custom features',
            'White labeling (coming soon)'
        ],
        'monthly_price': 10000,
        'lifetime_price': 150000  # 10 months equivalent
    }
}

# M-Pesa configuration
MPESA_CONFIG = {
    'CONSUMER_KEY': '',  # Add your Safaricom app consumer key
    'CONSUMER_SECRET': '',  # Add your Safaricom app consumer secret
    'SHORTCODE': '',  # Your M-Pesa shortcode (Paybill/Till)
    'PASSKEY': '',  # Your M-Pesa passkey
    'CALLBACK_URL': 'https://yourdomain.com/mpesa/callback',  # Your callback URL
}