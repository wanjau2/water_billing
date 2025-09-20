# subscription_renewal.py
from datetime import datetime, timedelta
from home import mongo, send_message, mpesa
from subscription_config import SUBSCRIPTION_TIERS
import logging
import os

def process_subscription_renewals():
    """Process auto-renewals for monthly and annual subscriptions"""
    try:
        # Find subscriptions expiring today with auto-renew enabled
        today = datetime.utcnow().date()
        
        expiring_subscriptions = mongo.db.admins.find({
            "subscription_type": {"$in": ["monthly", "annual"]},
            "auto_renew": True,
            "subscription_status": "active",
            "subscription_end_date": {
                "$gte": datetime.combine(today, datetime.min.time()),
                "$lt": datetime.combine(today + timedelta(days=1), datetime.min.time())
            }
        })
        
        for admin in expiring_subscriptions:
            try:
                tier = admin['subscription_tier']
                subscription_type = admin['subscription_type']

                # Get the correct price based on subscription type
                if subscription_type == 'annual':
                    amount = SUBSCRIPTION_TIERS[tier]['annual_price']
                else:
                    amount = SUBSCRIPTION_TIERS[tier]['monthly_price']
                
                if amount > 0 and admin.get('phone'):
                    # Initiate M-Pesa payment
                    reference = f"RENEWAL-{admin['_id']}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
                    
                    response = mpesa.stk_push(
                        phone_number=admin['phone'],
                        amount=amount,
                        account_reference=reference,
                        callback_url=os.getenv('MPESA_CALLBACK_URL')
                    )
                    
                    if 'error' not in response:
                        # Record renewal attempt
                        mongo.db.subscription_payments.insert_one({
                            'admin_id': admin['_id'],
                            'reference': reference,
                            'tier': tier,
                            'payment_type': subscription_type,
                            'amount': amount,
                            'phone_number': admin['phone'],
                            'checkout_request_id': response.get('CheckoutRequestID'),
                            'status': 'pending',
                            'is_renewal': True,
                            'created_at': datetime.utcnow()
                        })
                        
                        # Send SMS notification
                        message = f"Your {SUBSCRIPTION_TIERS[tier]['name']} subscription is due for renewal. We've sent an M-Pesa request for KES {amount}. Enter your PIN to continue enjoying our services."
                        send_message(admin['phone'], message)
                        
                        logging.info(f"Renewal initiated for admin {admin['_id']}")
                    else:
                        logging.error(f"Renewal failed for admin {admin['_id']}: {response['error']}")
                        
            except Exception as e:
                logging.error(f"Error processing renewal for admin {admin['_id']}: {e}")
                
        # Send reminders for subscriptions expiring in 3 days
        remind_date = today + timedelta(days=3)
        
        upcoming_expirations = mongo.db.admins.find({
            "subscription_type": {"$in": ["monthly", "annual"]},
            "subscription_status": "active",
            "subscription_end_date": {
                "$gte": datetime.combine(remind_date, datetime.min.time()),
                "$lt": datetime.combine(remind_date + timedelta(days=1), datetime.min.time())
            }
        })
        
        for admin in upcoming_expirations:
            if admin.get('phone'):
                tier_name = SUBSCRIPTION_TIERS[admin['subscription_tier']]['name']
                message = f"Your {tier_name} subscription expires in 3 days. "
                
                if admin.get('auto_renew'):
                    message += "Auto-renewal is enabled."
                else:
                    message += "Please renew to avoid service interruption."
                    
                send_message(admin['phone'], message)
                
    except Exception as e:
        logging.error(f"Subscription renewal job error: {e}")

if __name__ == "__main__":
    process_subscription_renewals()