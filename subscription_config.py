# subscription_config.py
SUBSCRIPTION_TIERS = {
    'starter': {
        'name': 'Starter',
        'max_tenants': 15,
        'max_houses': 20,  # Assuming a limit of 10 houses for starter tier
        'features': [
            'Up to 15 tenants',
            'Basic water billing',
            'SMS notifications',
            'Basic reports'
        ],
        'monthly_price': 300,
        'lifetime_price': 10000
    },
    'basic': {
        'name': 'Basic',
        'max_tenants': 50,
        'max_houses': -1,
        'features': [
            'Up to 20 tenants',
            'Water & rent billing',
            'SMS notifications',
            'Excel import/export',
            'Payment tracking'
        ],
        'monthly_price': 600,
        'lifetime_price': 25000  # 10 months equivalent
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
        'lifetime_price': 55000  # 10 months equivalent
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
        'lifetime_price': 90000  # 10 months equivalent
    },
    'enterprise': {
        'name': 'Enterprise',
        'max_tenants': 1000,  # Unlimited
        'max_houses': -1,
        'features': [
            '1000 tenants',
            'All Enterprise features',
            'Dedicated support',
            'Custom features',
            'White labeling (coming soon)'
        ],
        'monthly_price': 10000,
        'lifetime_price': 250000  # 10 months equivalent
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