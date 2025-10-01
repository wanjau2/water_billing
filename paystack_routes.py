# paystack_routes.py
"""
Paystack Payment Routes
These routes handle Paystack payments for subscriptions, water billing, and rent
"""

from flask import request, redirect, url_for, flash, session, jsonify, render_template
from paystack_integration import PaystackAPI
from datetime import datetime
from dateutil.relativedelta import relativedelta
import os

# Initialize Paystack API
PAYSTACK_SECRET_KEY = os.getenv('PAYSTACK_SECRET_KEY', '')
PAYSTACK_PUBLIC_KEY = os.getenv('PAYSTACK_PUBLIC_KEY', '')
PAYSTACK_ENV = os.getenv('PAYSTACK_ENV', 'test')

paystack_api = PaystackAPI(
    secret_key=PAYSTACK_SECRET_KEY,
    public_key=PAYSTACK_PUBLIC_KEY,
    env=PAYSTACK_ENV
)


def init_paystack_routes(app, mongo, csrf):
    """
    Initialize Paystack routes

    Args:
        app: Flask application instance
        mongo: MongoDB instance
        csrf: CSRF protection instance
    """

    @app.route('/paystack/initiate-subscription-payment', methods=['POST'])
    def paystack_initiate_subscription_payment():
        """Initiate subscription payment via Paystack"""
        from services.auth import login_required, get_admin_id

        if 'admin_id' not in session:
            flash('Please login to continue', 'danger')
            return redirect(url_for('login'))

        try:
            admin_id = get_admin_id()
            admin = mongo.db.admins.find_one({"_id": admin_id})

            if not admin:
                flash('Admin account not found', 'danger')
                return redirect(url_for('subscription'))

            # Get form data
            tier = request.form.get('tier')
            payment_type = request.form.get('payment_type', 'monthly')  # monthly or annual

            # Validate tier
            from subscription_config import SUBSCRIPTION_TIERS
            if tier not in SUBSCRIPTION_TIERS:
                flash('Invalid subscription tier', 'danger')
                return redirect(url_for('subscription'))

            tier_config = SUBSCRIPTION_TIERS[tier]

            # Determine amount based on payment type
            if payment_type == 'annual':
                amount = tier_config['annual_price']
            else:
                amount = tier_config['monthly_price']

            # Generate unique reference
            reference = f"SUB-{admin_id}-{tier}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

            # Get admin email
            email = admin.get('email', admin.get('username', 'noreply@example.com'))

            # Metadata for tracking
            metadata = {
                'admin_id': str(admin_id),
                'tier': tier,
                'payment_type': payment_type,
                'admin_name': admin.get('name', 'N/A'),
                'phone': admin.get('phone', 'N/A'),
                'purpose': 'subscription_payment'
            }

            # Initialize transaction with Paystack
            callback_url = url_for('paystack_subscription_callback', _external=True)

            result = paystack_api.initialize_transaction(
                email=email,
                amount=amount,
                reference=reference,
                callback_url=callback_url,
                metadata=metadata,
                channels=['mobile_money', 'card']  # Allow both M-Pesa and cards
            )

            if result['success']:
                # Store payment record
                mongo.db.subscription_payments.insert_one({
                    'admin_id': admin_id,
                    'reference': reference,
                    'tier': tier,
                    'payment_type': payment_type,
                    'amount': amount,
                    'status': 'pending',
                    'provider': 'paystack',
                    'created_at': datetime.now(),
                    'authorization_url': result['authorization_url']
                })

                app.logger.info(f"Paystack subscription payment initiated: {reference}")

                # Redirect to Paystack payment page
                return redirect(result['authorization_url'])

            else:
                flash(f'Payment initialization failed: {result.get("error")}', 'danger')
                return redirect(url_for('subscription'))

        except Exception as e:
            app.logger.error(f"Paystack subscription payment error: {str(e)}")
            flash('An error occurred while initiating payment', 'danger')
            return redirect(url_for('subscription'))

    @app.route('/paystack/subscription-callback')
    def paystack_subscription_callback():
        """Handle Paystack subscription payment callback"""
        reference = request.args.get('reference')

        if not reference:
            flash('Invalid payment reference', 'danger')
            return redirect(url_for('subscription'))

        try:
            # Verify transaction with Paystack
            result = paystack_api.verify_transaction(reference)

            if not result['success']:
                flash(f'Payment verification failed: {result.get("error")}', 'danger')
                return redirect(url_for('subscription'))

            # Get payment record
            payment = mongo.db.subscription_payments.find_one({'reference': reference})

            if not payment:
                flash('Payment record not found', 'danger')
                return redirect(url_for('subscription'))

            admin_id = payment['admin_id']
            tier = payment['tier']
            payment_type = payment['payment_type']
            amount = payment['amount']

            # Check if payment was successful
            if result['status'] == 'success':
                # Verify amount matches
                if result['amount'] != amount:
                    app.logger.warning(f"Amount mismatch for {reference}: Expected {amount}, got {result['amount']}")

                # Update payment record
                mongo.db.subscription_payments.update_one(
                    {'reference': reference},
                    {'$set': {
                        'status': 'completed',
                        'completed_at': datetime.now(),
                        'channel': result.get('channel'),
                        'fees': result.get('fees', 0)
                    }}
                )

                # Calculate subscription dates
                start_date = datetime.now()
                if payment_type == 'annual':
                    end_date = start_date + relativedelta(years=1)
                else:
                    end_date = start_date + relativedelta(months=1)

                # Update admin subscription
                mongo.db.admins.update_one(
                    {"_id": admin_id},
                    {"$set": {
                        "subscription_tier": tier,
                        "subscription_type": payment_type,
                        "subscription_status": "active",
                        "subscription_start_date": start_date,
                        "subscription_end_date": end_date,
                        "last_payment_date": start_date
                    }}
                )

                app.logger.info(f"Subscription activated via Paystack: {admin_id} - {tier}")

                flash(f'Payment successful! Your {tier.title()} subscription is now active.', 'success')
                return redirect(url_for('subscription'))

            elif result['status'] == 'failed':
                # Update payment record
                mongo.db.subscription_payments.update_one(
                    {'reference': reference},
                    {'$set': {
                        'status': 'failed',
                        'failed_at': datetime.now()
                    }}
                )

                flash('Payment failed. Please try again.', 'danger')
                return redirect(url_for('subscription'))

            else:
                # Abandoned or pending
                flash('Payment was not completed. Please try again.', 'warning')
                return redirect(url_for('subscription'))

        except Exception as e:
            app.logger.error(f"Paystack callback error: {str(e)}")
            flash('An error occurred while processing your payment', 'danger')
            return redirect(url_for('subscription'))

    @app.route('/paystack/webhook', methods=['POST'])
    @csrf.exempt  # Paystack webhooks can't include CSRF token
    def paystack_webhook():
        """
        Handle Paystack webhook events
        This is called by Paystack for real-time payment notifications
        """
        try:
            # Get signature from header
            signature = request.headers.get('X-Paystack-Signature')

            if not signature:
                app.logger.warning('Paystack webhook received without signature')
                return jsonify({'status': 'error', 'message': 'No signature'}), 400

            # Verify webhook signature
            payload = request.get_data()

            if not paystack_api.verify_webhook_signature(payload, signature):
                app.logger.warning('Paystack webhook signature verification failed')
                return jsonify({'status': 'error', 'message': 'Invalid signature'}), 400

            # Parse webhook data
            data = request.get_json()
            event = data.get('event')
            event_data = data.get('data', {})

            app.logger.info(f"Paystack webhook received: {event}")

            # Handle different webhook events
            if event == 'charge.success':
                # Payment succeeded
                reference = event_data.get('reference')
                amount = event_data.get('amount') / 100  # Convert from kobo
                status = event_data.get('status')
                channel = event_data.get('channel')
                metadata = event_data.get('metadata', {})

                # Find payment record
                payment = mongo.db.subscription_payments.find_one({'reference': reference})

                if payment and payment.get('status') == 'pending':
                    # Update payment status
                    mongo.db.subscription_payments.update_one(
                        {'reference': reference},
                        {'$set': {
                            'status': 'completed',
                            'completed_at': datetime.now(),
                            'channel': channel,
                            'webhook_received': True
                        }}
                    )

                    app.logger.info(f"Payment confirmed via webhook: {reference}")

                return jsonify({'status': 'success'}), 200

            elif event == 'charge.failed':
                # Payment failed
                reference = event_data.get('reference')

                mongo.db.subscription_payments.update_one(
                    {'reference': reference},
                    {'$set': {
                        'status': 'failed',
                        'failed_at': datetime.now(),
                        'webhook_received': True
                    }}
                )

                return jsonify({'status': 'success'}), 200

            else:
                # Other events (transfer.success, transfer.failed, etc.)
                app.logger.info(f"Unhandled webhook event: {event}")
                return jsonify({'status': 'success'}), 200

        except Exception as e:
            app.logger.error(f"Paystack webhook error: {str(e)}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/paystack/initiate-water-payment', methods=['POST'])
    def paystack_initiate_water_payment():
        """Initiate water billing payment via Paystack"""
        from services.auth import login_required, get_admin_id

        if 'admin_id' not in session:
            return jsonify({'success': False, 'error': 'Not authenticated'}), 401

        try:
            # Get payment details
            tenant_id = request.form.get('tenant_id')
            amount = float(request.form.get('amount', 0))
            bill_ids = request.form.getlist('bill_ids[]')  # Multiple bills can be paid

            if not tenant_id or amount <= 0:
                return jsonify({'success': False, 'error': 'Invalid payment details'}), 400

            # Get tenant details
            from bson.objectid import ObjectId
            tenant = mongo.db.tenants.find_one({'_id': ObjectId(tenant_id)})

            if not tenant:
                return jsonify({'success': False, 'error': 'Tenant not found'}), 404

            # Generate reference
            reference = f"WATER-{tenant_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

            # Get tenant email (use placeholder if not available)
            email = tenant.get('email', f"{tenant.get('name', 'tenant')}@placeholder.com".replace(' ', ''))

            metadata = {
                'tenant_id': str(tenant_id),
                'tenant_name': tenant.get('name'),
                'admin_id': str(get_admin_id()),
                'bill_ids': bill_ids,
                'purpose': 'water_payment'
            }

            callback_url = url_for('paystack_water_callback', _external=True)

            result = paystack_api.initialize_transaction(
                email=email,
                amount=amount,
                reference=reference,
                callback_url=callback_url,
                metadata=metadata,
                channels=['mobile_money', 'card']
            )

            if result['success']:
                # Store payment record
                mongo.db.water_payments.insert_one({
                    'tenant_id': ObjectId(tenant_id),
                    'admin_id': get_admin_id(),
                    'reference': reference,
                    'amount': amount,
                    'bill_ids': [ObjectId(bid) for bid in bill_ids],
                    'status': 'pending',
                    'provider': 'paystack',
                    'created_at': datetime.now()
                })

                return jsonify({
                    'success': True,
                    'authorization_url': result['authorization_url'],
                    'reference': reference
                })

            else:
                return jsonify({'success': False, 'error': result.get('error')}), 400

        except Exception as e:
            app.logger.error(f"Water payment initiation error: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/paystack/water-callback')
    def paystack_water_callback():
        """Handle water payment callback"""
        reference = request.args.get('reference')

        if not reference:
            flash('Invalid payment reference', 'danger')
            return redirect(url_for('water_utility'))

        try:
            # Verify transaction
            result = paystack_api.verify_transaction(reference)

            if result['success'] and result['status'] == 'success':
                # Update payment record
                payment = mongo.db.water_payments.find_one({'reference': reference})

                if payment:
                    # Mark bills as paid
                    for bill_id in payment.get('bill_ids', []):
                        mongo.db.bills.update_one(
                            {'_id': bill_id},
                            {'$set': {
                                'status': 'paid',
                                'paid_at': datetime.now(),
                                'payment_reference': reference
                            }}
                        )

                    # Update payment record
                    mongo.db.water_payments.update_one(
                        {'reference': reference},
                        {'$set': {
                            'status': 'completed',
                            'completed_at': datetime.now(),
                            'channel': result.get('channel')
                        }}
                    )

                    flash('Payment successful!', 'success')
                else:
                    flash('Payment record not found', 'warning')

            else:
                flash('Payment verification failed', 'danger')

            return redirect(url_for('water_utility'))

        except Exception as e:
            app.logger.error(f"Water payment callback error: {str(e)}")
            flash('An error occurred processing your payment', 'danger')
            return redirect(url_for('water_utility'))

    app.logger.info("Paystack routes initialized successfully")


# Export for use in home.py
__all__ = ['init_paystack_routes', 'paystack_api']
