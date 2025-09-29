# subscription_config.py
SUBSCRIPTION_TIERS = {
    'starter': {
        'name': 'Starter',
        'max_tenants': 20,
        'max_houses': 20,  # Assuming a limit of 10 houses for starter tier
        'features': [
            'Up to 15 tenants',
            'Basic water billing',
            'SMS notifications',
            'Basic reports'
        ],
        'monthly_price': 1500,
        'annual_price': 15000  # 10 months equivalent (save 2 months)
    },
    'basic': {
        'name': 'Basic',
        'max_tenants': 50,
        'max_houses': -1,
        'features': [
            'Up to 50 tenants',
            'Water & rent billing',
            'SMS notifications',
            'Excel import/export',
            'Payment tracking'
        ],
        'monthly_price': 3000,
        'annual_price': 25000  # 10 months equivalent (save 2 months)
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
        'monthly_price': 6000,
        'annual_price': 55000  # 10 months equivalent (save 2 months)
    },
    'business': {
        'name': 'Business',
        'max_tenants': 250,
        'max_houses': -1,

        'features': [
            'Up to 250 tenants',
            'All Pro features',
            'Priority support',
            'API access (coming soon)'
        ],
        'monthly_price': 15000,
        'annual_price': 145000  # 10 months equivalent (save 2 months)
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
        'monthly_price': 60000,
        'annual_price': 400000  # 10 months equivalent (save 2 months)
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