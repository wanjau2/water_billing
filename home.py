import os
import logging
import urllib.parse
import re
import requests
import dns.resolver
from mpesa_integration import MpesaAPI
from subscription_config import SUBSCRIPTION_TIERS, MPESA_CONFIG
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from functools import wraps
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
# from flask_pymongo import PyMongo  # Comment out as we'll use direct MongoClient
from bson.objectid import ObjectId
import pandas as pd
from werkzeug.utils import secure_filename
from io import BytesIO
from flask import send_file
from bson import ObjectId
import xlsxwriter
import pymongo
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from flask_caching import Cache


    
# Load environment variables from the .env file
load_dotenv()

# Environment variables with fallbacks
TALKSASA_API_KEY = os.environ.get("TALKSASA_API_KEY")
TALKSASA_SENDER_ID = os.environ.get("TALKSASA_SENDER_ID", "WATER")
SECRET_KEY = os.environ.get("SECRET_KEY")
RATE_PER_UNIT = float(os.environ.get("RATE_PER_UNIT", 100))
DEFAULT_PER_PAGE = int(os.environ.get("DEFAULT_PER_PAGE", 10))
# MongoDB connection parameters
MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME")

# Create Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
# Initialize logging to file
handler = logging.FileHandler('app.log')
handler.setLevel(logging.INFO)
app.logger.addHandler(handler)
csrf = CSRFProtect(app)
mongo = None

# Define a proper Content Security Policy
csp = {
    'default-src': [
        "'self'",
        # Add any other domains you trust for default resources
    ],
    # Permit loading CSS from your own domain and the CDN
    'style-src': [
        "'self'",
        "'unsafe-inline'",  # needed for inline Bootstrap if any
        'https://cdn.jsdelivr.net'
    ],
    # If you also load scripts from a CDN, configure script-src similarly
    'script-src': [
        "'self'",
        'https://cdn.jsdelivr.net'
    ],
}
cache_config = {
    "CACHE_TYPE": "SimpleCache",  # Use SimpleCache for in-memory caching
    "CACHE_DEFAULT_TIMEOUT": 3600  # Cache timeout in seconds (1 hour)
}
app.config.from_mapping(cache_config)
cache = Cache(app)
#Talisman(app, content_security_policy=csp)

limiter = Limiter(key_func=get_remote_address, default_limits=["200 per day", "50 per hour"],app=app,storage_uri="memory://")

dns.resolver.default_resolver=dns.resolver.Resolver(configure=True)
dns.resolver.default_resolver.nameservers = ['8.8.8.8']
# Initialize MongoDB connection using the same approach as testmongo.py
try:
    # Create a new client and connect to the server
    client = MongoClient(MONGO_URI, server_api=ServerApi('1'))
    
    # Test the connection by accessing the database
    client.admin.command('ping')
    app.logger.info("MongoDB connection successful")
    
    # Create a wrapper object similar to what PyMongo provides
    # Specify the database name explicitly
    db = client.get_database(DATABASE_NAME)  # Use explicit database name
    mongo = type('obj', (object,), {'db': db})
    
except pymongo.errors.ConnectionFailure as e:
    app.logger.error(f"MongoDB connection failed: {e}")
    print(f"MongoDB connection failed: {e}")
except pymongo.errors.ConfigurationError as e:
    app.logger.error(f"MongoDB configuration error: {e}")
    print(f"MongoDB configuration error: {e}")
except Exception as e:
    app.logger.error(f"Database initialization error: {e}")
    print(f"Database initialization error: {e}")

if mongo is None:
    app.logger.critical("MongoDB did not initialize—check MONGO_URI!")
    raise RuntimeError("Failed to connect to MongoDB")

# Function to check database connection
def is_db_connected():
    if mongo is None:
        return False
    try:
        # Quick ping to check connection
        mongo.db.command('ping')
        return True
    except Exception:
        return False

# Initialize M-Pesa API
mpesa = MpesaAPI(
    consumer_key=os.getenv('MPESA_CONSUMER_KEY', MPESA_CONFIG['CONSUMER_KEY']),
    consumer_secret=os.getenv('MPESA_CONSUMER_SECRET', MPESA_CONFIG['CONSUMER_SECRET']),
    shortcode=os.getenv('MPESA_SHORTCODE', MPESA_CONFIG['SHORTCODE']),
    passkey=os.getenv('MPESA_PASSKEY', MPESA_CONFIG['PASSKEY']),
    env=os.getenv('MPESA_ENV', 'sandbox')
)

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            flash('Please log in to access this page', 'warning')
            return redirect(url_for('login'))
        
        # Add database connection check
        if not is_db_connected():
            flash('Database connection error. Please try again later.', 'danger')
            return redirect(url_for('db_error'))
            
        return f(*args, **kwargs)

    return decorated_function

def initialize_subscription_records():
    """Create subscription collection and records"""
    try:
        # Create indexes for subscription_payments collection
        if 'subscription_payments' not in mongo.db.list_collection_names():
            mongo.db.create_collection('subscription_payments')
            
        mongo.db.subscription_payments.create_index([
            ('admin_id', 1),
            ('created_at', -1)
        ])
        
        mongo.db.subscription_payments.create_index([
            ('checkout_request_id', 1)
        ], unique=True, sparse=True)
        
        # Update admin schema for subscriptions
        mongo.db.admins.update_many(
            {"subscription_type": {"$exists": False}},
            {"$set": {
                "subscription_type": "monthly",  # monthly or lifetime
                "subscription_tier": "starter",
                "subscription_status": "active",
                "subscription_start_date": datetime.now(),
                "subscription_end_date": None,  # None for lifetime
                "auto_renew": True,
                "last_payment_date": None,
                "next_billing_date": None
            }}
        )
        
        app.logger.info("Subscription records initialized")
        return True
        
    except Exception as e:
        app.logger.error(f"Error initializing subscription records: {e}")
        return False

def check_subscription_limit(resource_type='tenant'):
    """Decorator to check subscription limits before adding resources"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            try:
                admin_id = get_admin_id()
                admin = mongo.db.admins.find_one({"_id": admin_id})
                
                if not admin:
                    flash('Admin account not found', 'danger')
                    return redirect(url_for('dashboard'))
                
                # Get subscription tier
                tier = admin.get('subscription_tier', 'starter')
                subscription_status = admin.get('subscription_status', 'active')
                subscription_type = admin.get('subscription_type', 'monthly')
                
                # Check if subscription is active
                if subscription_status != 'active' and tier != 'starter':
                    flash('Your subscription is inactive. Please renew to continue.', 'danger')
                    return redirect(url_for('subscription'))
                
                # Check expiry for monthly subscriptions
                if subscription_type == 'monthly':
                    end_date = admin.get('subscription_end_date')
                    if end_date and end_date < datetime.now() and tier != 'starter':
                        # Downgrade to starter tier
                        mongo.db.admins.update_one(
                            {"_id": admin_id},
                            {"$set": {
                                "subscription_tier": "starter",
                                "subscription_status": "expired"
                            }}
                        )
                        tier = 'starter'
                        flash('Your subscription has expired. You have been downgraded to the Starter plan.', 'warning')
                
                # Get limits for the tier
                tier_config = SUBSCRIPTION_TIERS.get(tier, SUBSCRIPTION_TIERS['starter'])
                
                if resource_type == 'tenant':
                    max_allowed = tier_config['max_tenants']
                    current_count = mongo.db.tenants.count_documents({"admin_id": admin_id})
                    resource_name = 'tenants'
                elif resource_type == 'house':
                    max_allowed = tier_config['max_houses']
                    current_count = mongo.db.houses.count_documents({"admin_id": admin_id})
                    resource_name = 'houses'
                else:
                    return f(*args, **kwargs)
                
                # Check limit (-1 means unlimited)
                if max_allowed != -1 and current_count >= max_allowed:
                    flash(
                        f'You have reached the maximum number of {resource_name} ({max_allowed}) '
                        f'for your {tier_config["name"]} plan. Please upgrade to add more.',
                        'warning'
                    )
                    return redirect(url_for('subscription'))
                
                return f(*args, **kwargs)
                
            except Exception as e:
                app.logger.error(f"Subscription check error: {e}")
                # Allow the operation in case of error
                return f(*args, **kwargs)
                
        return decorated_function
    return decorator 

def initialize_subscriptions():
    """Initialize subscription data for existing admins"""
    try:
        # Add subscription fields to existing admins
        admins_without_subscription = mongo.db.admins.find({
            "$or": [
                {"subscription_tier": {"$exists": False}},
                {"subscription_tier": None}
            ]
        })
        
        for admin in admins_without_subscription:
            # Count current tenants for this admin
            tenant_count = mongo.db.tenants.count_documents({"admin_id": admin["_id"]})
            
            # Assign appropriate tier based on current usage
            if tenant_count <= 5:
                tier = 'starter'
            elif tenant_count <= 20:
                tier = 'basic'
            elif tenant_count <= 100:
                tier = 'pro'
            elif tenant_count <= 250:
                tier = 'business'
            else:
                tier = 'enterprise'
            
            # Update admin with subscription info
            mongo.db.admins.update_one(
                {"_id": admin["_id"]},
                {"$set": {
                    "subscription_tier": tier,
                    "subscription_start_date": datetime.now(),
                    "subscription_end_date": datetime.now() + relativedelta(months=1),
                    "subscription_status": "active",
                    "trial_ends_at": datetime.now() + timedelta(days=14)  # 14-day trial
                }}
            )
            
        app.logger.info("Subscription initialization completed")
        return True
        
    except Exception as e:
        app.logger.error(f"Error initializing subscriptions: {e}")
        return False


def initialize_houses_collection():
    """Initialize houses collection with all existing house numbers"""
    try:
        # Check if houses collection exists
        if 'houses' not in mongo.db.list_collection_names():
            mongo.db.create_collection('houses')
            app.logger.info("Houses collection created")
        
        # Get all distinct house numbers from tenants collection
        # Group by admin_id to maintain isolation
        admin_tenant_map = {}
        for tenant in mongo.db.tenants.find({}, {"house_number": 1, "admin_id": 1}):
            if not tenant.get("house_number"):
                continue
                
            admin_id = tenant.get("admin_id")
            if admin_id not in admin_tenant_map:
                admin_tenant_map[admin_id] = set()
            admin_tenant_map[admin_id].add(tenant["house_number"])
        
        # Process houses for each admin separately
        for admin_id, houses in admin_tenant_map.items():
            for house_number in houses:
                # Check if house already exists in houses collection for this admin
                existing = mongo.db.houses.find_one({
                    "house_number": house_number,
                    "admin_id": admin_id
                })
                
                if not existing:
                    # Get tenant info for this house and admin
                    tenant = mongo.db.tenants.find_one({
                        "house_number": house_number,
                        "admin_id": admin_id
                    })

                    if tenant:
                        # Create house record with proper admin_id
                        house_data = {
                            "house_number": house_number,
                            "is_occupied": True,
                            "current_tenant_id": tenant["_id"],
                            "current_tenant_name": tenant["name"],
                            "admin_id": admin_id,
                            "created_at": datetime.now(),
                            "rent": 0
                        }
                        mongo.db.houses.insert_one(house_data)
                        app.logger.info(f"Added house {house_number} for admin {admin_id} to houses collection")
        
        app.logger.info("Houses collection initialized successfully")
        return True
    except Exception as e:
        app.logger.error(f"Error initializing houses collection: {e}")
        return False


def migrate_existing_readings_to_payments():
    """One-time migration to create payment records for existing readings"""
    try:
        # Get all existing water readings that don't have payment records
        existing_readings = list(mongo.db.water_readings.find({}))
        
        for reading in existing_readings:
            # Check if payment record already exists
            existing_payment = mongo.db.payments.find_one({
                'reading_id': reading['_id']
            })
            
            if not existing_payment:
                # Create payment record
                reading_date = reading.get('reading_date', datetime.now())
                month_year = reading_date.strftime("%Y-%m")
                
                payment_data = {
                    'admin_id': reading['admin_id'],
                    'tenant_id': reading['tenant_id'],
                    'house_id': reading.get('house_id'),
                    'bill_amount': reading.get('bill_amount', 0),
                    'amount_paid': 0.0,
                    'payment_status': 'unpaid',
                    'due_date': reading_date + timedelta(days=30),
                    'month_year': month_year,
                    'reading_id': reading['_id'],
                    'last_payment_date': None,
                    'last_payment_method': None,
                    'notes': 'Migrated from existing reading',
                    'created_at': reading_date,
                    'updated_at': datetime.now()
                }
                
                mongo.db.payments.insert_one(payment_data)
                app.logger.info(f"Migrated payment record for reading {reading['_id']}")
        
        app.logger.info("Migration completed successfully")
        return True
        
    except Exception as e:
        app.logger.error(f"Migration error: {str(e)}")
        return False

# Add this migration function
def migrate_to_meter_readings():
    """Migrate data from water_readings and house_readings to meter_readings."""
    try:
        # Create meter_readings collection with proper indexing
        if 'meter_readings' not in mongo.db.list_collection_names():
            mongo.db.create_collection('meter_readings')
            
        # Create indexes for optimal performance
        mongo.db.meter_readings.create_index([
            ('tenant_id', 1),
            ('admin_id', 1),
            ('date_recorded', -1)
        ], name='tenant_admin_date_idx')
        
        mongo.db.meter_readings.create_index([
            ('house_number', 1),
            ('admin_id', 1),
            ('date_recorded', -1)
        ], name='house_admin_date_idx')
        
        mongo.db.meter_readings.create_index([
            ('house_id', 1),
            ('date_recorded', -1)
        ], name='house_id_date_idx')
        
        # Add compound index for payment queries
        mongo.db.meter_readings.create_index([
            ('admin_id', 1),
            ('reading_type', 1),
            ('date_recorded', -1)
        ], name='admin_type_date_idx')
        
        # Migrate water_readings data
        water_readings = list(mongo.db.water_readings.find({}))
        for reading in water_readings:
            reading['source_collection'] = 'water_readings'
            reading['reading_type'] = 'tenant_billing'
            
        # Migrate house_readings data
        house_readings = list(mongo.db.house_readings.find({}))
        for reading in house_readings:
            reading['source_collection'] = 'house_readings'
            reading['reading_type'] = 'house_history'
            
        # Insert all readings into meter_readings (avoid duplicates)
        all_readings = water_readings + house_readings
        if all_readings:
            # Remove duplicates based on key fields
            unique_readings = []
            seen = set()
            for reading in all_readings:
                key = (str(reading.get('tenant_id', '')), 
                      str(reading.get('house_number', '')), 
                      str(reading.get('date_recorded', '')))
                if key not in seen:
                    seen.add(key)
                    unique_readings.append(reading)
            
            mongo.db.meter_readings.insert_many(unique_readings)
            
        app.logger.info(f"Migrated {len(all_readings)} readings to meter_readings collection")
        return True
        
    except Exception as e:
        app.logger.error(f"Migration failed: {str(e)}")
        return False


def get_current_month_year():
    """Get current month-year in consistent format"""
    return datetime.now().strftime('%Y-%m')

@app.route('/migrate_timestamps', methods=['POST','GET'])
@login_required
def migrate_timestamps():
    """One-time migration to add timestamps to existing date-only records"""
    try:
        admin_id = get_admin_id()
        
        # Find records with date-only values (assuming they don't have time component)
        records_to_update = list(mongo.db.meter_readings.find({
            "admin_id": admin_id,
            "$expr": {
                "$eq": [
                    {"$dateToString": {"format": "%H:%M:%S", "date": "$date_recorded"}},
                    "00:00:00"
                ]
            }
        }))
        
        updated_count = 0
        
        for record in records_to_update:
            # Add random minutes/seconds to spread out same-day readings
            import random
            original_date = record['date_recorded']
            
            # Add random time between 8 AM and 6 PM for realistic spread
            random_hour = random.randint(8, 18)
            random_minute = random.randint(0, 59)
            random_second = random.randint(0, 59)
            
            new_timestamp = original_date.replace(
                hour=random_hour,
                minute=random_minute,
                second=random_second
            )
            
            # Update the record
            mongo.db.meter_readings.update_one(
                {"_id": record["_id"]},
                {"$set": {"date_recorded": new_timestamp}}
            )
            updated_count += 1
        
        flash(f"Updated {updated_count} records with proper timestamps", "success")
        
    except Exception as e:
        app.logger.error(f"Error in timestamp migration: {str(e)}")
        flash(f"Migration error: {str(e)}", "danger")
    
    return redirect(url_for('dashboard'))

# Add migration route for admin use
@app.route('/migrate_collections', methods=['POST'])
@login_required
def migrate_collections():
    """Admin route to migrate to meter_readings collection."""
    try:
        admin_id = get_admin_id()
        
        # Only allow super admin or specific admin
        admin = mongo.db.admins.find_one({"_id": admin_id})
        if not admin or not admin.get('is_super_admin', False):
            flash('Unauthorized access', 'danger')
            return redirect(url_for('dashboard'))
        
        success = migrate_to_meter_readings()
        
        if success:
            flash('Successfully migrated to meter_readings collection', 'success')
        else:
            flash('Migration failed. Check logs for details.', 'danger')
            
    except Exception as e:
        flash(f'Migration error: {str(e)}', 'danger')
        
    return redirect(url_for('dashboard'))

def migrate_existing_data():
    """Migrate existing data to include admin_id field"""
    try:
        app.logger.info("Starting data migration to add admin_id field to existing records")
        
        # Get all admins
        admins = list(mongo.db.admins.find())
        
        if not admins:
            app.logger.warning("No admins found for migration")
            return False
            
        # Use the first admin as default for existing records
        default_admin_id = admins[0]["_id"]
        app.logger.info(f"Using admin ID {default_admin_id} as default for existing records")
        
        # Update tenants collection
        tenants_result = mongo.db.tenants.update_many(
            {"admin_id": {"$exists": False}},
            {"$set": {"admin_id": default_admin_id}}
        )
        app.logger.info(f"Updated {tenants_result.modified_count} tenant records")
        
        # Update houses collection
        houses_result = mongo.db.houses.update_many(
            {"admin_id": {"$exists": False}},
            {"$set": {"admin_id": default_admin_id}}
        )
        app.logger.info(f"Updated {houses_result.modified_count} house records")
        
        # Update water_readings collection
        readings_result = mongo.db.water_readings.update_many(
            {"admin_id": {"$exists": False}},
            {"$set": {"admin_id": default_admin_id}}
        )
        app.logger.info(f"Updated {readings_result.modified_count} water reading records")
        
        # Update sms_config collection
        sms_result = mongo.db.sms_config.update_many(
            {"admin_id": {"$exists": False}},
            {"$set": {"admin_id": default_admin_id}}
        )
        app.logger.info(f"Updated {sms_result.modified_count} SMS config records")
        
        app.logger.info("Data migration completed successfully")
        return True
    except Exception as e:
        app.logger.error(f"Error during data migration: {e}")
        return False


@app.route('/subscription')
@login_required
def subscription():
    """Enhanced subscription management page"""
    try:
        admin_id = get_admin_id()
        admin = mongo.db.admins.find_one({"_id": admin_id})
        
        # Get current usage
        tenant_count = mongo.db.tenants.count_documents({"admin_id": admin_id})
        house_count = mongo.db.houses.count_documents({"admin_id": admin_id})
        
        # Get subscription info
        current_tier_key = admin.get('subscription_tier', 'starter')
        current_tier = SUBSCRIPTION_TIERS.get(current_tier_key, SUBSCRIPTION_TIERS['starter'])
        subscription_type = admin.get('subscription_type', 'monthly')
        
        # Calculate days remaining for monthly subscriptions
        days_remaining = None
        if subscription_type == 'monthly' and admin.get('subscription_end_date'):
            days_remaining = (admin['subscription_end_date'] - datetime.now()).days
            
        # Get payment history
        payment_history = list(mongo.db.subscription_payments.find(
            {"admin_id": admin_id}
        ).sort("created_at", -1).limit(5))
        
        return render_template('subscription.html',
            subscription_tiers=SUBSCRIPTION_TIERS,
            current_subscription_tier=current_tier_key,
            current_tier=current_tier,
            subscription_type=subscription_type,
            tenant_count=tenant_count,
            house_count=house_count,
            subscription_status=admin.get('subscription_status', 'inactive'),
            subscription_end_date=admin.get('subscription_end_date'),
            auto_renew=admin.get('auto_renew', True),
            days_remaining=days_remaining,
            payment_history=payment_history,
            admin_phone=admin.get('phone', '')
        )
        
    except Exception as e:
        app.logger.error(f"Error loading subscription page: {e}")
        flash('Error loading subscription information', 'danger')
        return redirect(url_for('dashboard'))

@app.route('/upgrade_subscription', methods=['POST'])
@login_required
def upgrade_subscription():
    """Handle subscription upgrade/downgrade requests"""
    try:
        admin_id = get_admin_id()
        new_tier = request.form.get('tier')
        
        if new_tier not in SUBSCRIPTION_TIERS:
            flash('Invalid subscription tier', 'danger')
            return redirect(url_for('subscription'))
        
        # For demo purposes, automatically activate the subscription
        # In production, this would integrate with a payment gateway
        mongo.db.admins.update_one(
            {"_id": admin_id},
            {"$set": {
                "subscription_tier": new_tier,
                "subscription_status": "active",
                "subscription_start_date": datetime.now(),
                "subscription_end_date": datetime.now() + relativedelta(months=1)
            }}
        )
        
        flash(f'Successfully upgraded to {SUBSCRIPTION_TIERS[new_tier]["name"]} plan!', 'success')
        
    except Exception as e:
        app.logger.error(f"Error upgrading subscription: {e}")
        flash('Error processing subscription change', 'danger')
        
    return redirect(url_for('subscription'))

@app.route('/initiate_subscription_payment', methods=['POST'])
@login_required
def initiate_subscription_payment():
    """Initiate M-Pesa payment for subscription"""
    try:
        admin_id = get_admin_id()
        admin = mongo.db.admins.find_one({"_id": admin_id})
        
        # Get form data
        tier = request.form.get('tier')
        payment_type = request.form.get('payment_type', 'monthly')  # monthly or lifetime
        phone_number = request.form.get('phone_number', admin.get('phone', ''))
        
        if tier not in SUBSCRIPTION_TIERS:
            return jsonify({'error': 'Invalid subscription tier'}), 400
            
        # Format phone number
        try:
            phone_number = format_phone_number(phone_number)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
            
        # Get amount based on payment type
        tier_config = SUBSCRIPTION_TIERS[tier]
        if payment_type == 'lifetime':
            amount = tier_config['lifetime_price']
        else:
            amount = tier_config['monthly_price']
            
        if amount == 0:  # Free tier
            # Directly activate free subscription
            mongo.db.admins.update_one(
                {"_id": admin_id},
                {"$set": {
                    "subscription_tier": tier,
                    "subscription_type": payment_type,
                    "subscription_status": "active",
                    "subscription_start_date": datetime.now(),
                    "subscription_end_date": datetime.now() + relativedelta(months=1) if payment_type == 'monthly' else None,
                    "auto_renew": payment_type == 'monthly'
                }}
            )
            return jsonify({'success': True, 'message': 'Free tier activated'})
            
        # Generate unique reference
        reference = f"SUB-{admin_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # Initiate STK push
        callback_url = url_for('mpesa_callback', _external=True)
        response = mpesa.stk_push(
            phone_number=phone_number,
            amount=amount,
            account_reference=reference,
            callback_url=callback_url
        )
        
        if 'error' in response:
            return jsonify({'error': response['error']}), 400
            
        # Save payment record
        payment_record = {
            'admin_id': admin_id,
            'reference': reference,
            'tier': tier,
            'payment_type': payment_type,
            'amount': amount,
            'phone_number': phone_number,
            'checkout_request_id': response.get('CheckoutRequestID'),
            'merchant_request_id': response.get('MerchantRequestID'),
            'status': 'pending',
            'created_at': datetime.now()
        }
        
        mongo.db.subscription_payments.insert_one(payment_record)
        
        return jsonify({
            'success': True,
            'message': 'Payment initiated. Please check your phone for the M-Pesa prompt.',
            'checkout_request_id': response.get('CheckoutRequestID')
        })
        
    except Exception as e:
        app.logger.error(f"Payment initiation error: {e}")
        return jsonify({'error': 'Failed to initiate payment'}), 500

@app.route('/mpesa/callback', methods=['POST'])
@csrf.exempt  # M-Pesa callbacks can't send CSRF tokens
def mpesa_callback():
    """Handle M-Pesa payment callbacks"""
    try:
        # Get callback data
        data = request.get_json()
        
        # Extract relevant information
        result_code = data['Body']['stkCallback']['ResultCode']
        checkout_request_id = data['Body']['stkCallback']['CheckoutRequestID']
        
        # Find payment record
        payment = mongo.db.subscription_payments.find_one({
            'checkout_request_id': checkout_request_id
        })
        
        if not payment:
            app.logger.error(f"Payment record not found for {checkout_request_id}")
            return jsonify({'ResultCode': 1, 'ResultDesc': 'Payment not found'})
            
        if result_code == 0:  # Success
            # Extract payment details
            callback_metadata = data['Body']['stkCallback']['CallbackMetadata']['Item']
            
            amount = next(item['Value'] for item in callback_metadata if item['Name'] == 'Amount')
            receipt_number = next(item['Value'] for item in callback_metadata if item['Name'] == 'MpesaReceiptNumber')
            
            # Update payment record
            mongo.db.subscription_payments.update_one(
                {'_id': payment['_id']},
                {'$set': {
                    'status': 'completed',
                    'receipt_number': receipt_number,
                    'completed_at': datetime.now()
                }}
            )
            
            # Activate subscription
            admin_id = payment['admin_id']
            tier = payment['tier']
            payment_type = payment['payment_type']
            
            subscription_update = {
                "subscription_tier": tier,
                "subscription_type": payment_type,
                "subscription_status": "active",
                "subscription_start_date": datetime.now(),
                "last_payment_date": datetime.now(),
                "auto_renew": payment_type == 'monthly'
            }
            
            if payment_type == 'monthly':
                subscription_update["subscription_end_date"] = datetime.now() + relativedelta(months=1)
                subscription_update["next_billing_date"] = datetime.now() + relativedelta(months=1)
            else:  # lifetime
                subscription_update["subscription_end_date"] = None
                subscription_update["next_billing_date"] = None
                
            mongo.db.admins.update_one(
                {"_id": admin_id},
                {"$set": subscription_update}
            )
            
            # Send confirmation SMS
            admin = mongo.db.admins.find_one({"_id": admin_id})
            if admin and admin.get('phone'):
                tier_name = SUBSCRIPTION_TIERS[tier]['name']
                message = f"Payment confirmed! Your {tier_name} {payment_type} subscription is now active. Thank you for choosing Water Billing System."
                send_message(admin['phone'], message)
                
        else:  # Failed
            result_desc = data['Body']['stkCallback']['ResultDesc']
            
            mongo.db.subscription_payments.update_one(
                {'_id': payment['_id']},
                {'$set': {
                    'status': 'failed',
                    'error_message': result_desc,
                    'failed_at': datetime.now()
                }}
            )
            
        return jsonify({'ResultCode': 0, 'ResultDesc': 'Success'})
        
    except Exception as e:
        app.logger.error(f"M-Pesa callback error: {e}")
        return jsonify({'ResultCode': 1, 'ResultDesc': 'Internal error'})

@app.route('/check_payment_status/<checkout_request_id>')
@login_required
def check_payment_status(checkout_request_id):
    """Check payment status (for polling)"""
    try:
        payment = mongo.db.subscription_payments.find_one({
            'checkout_request_id': checkout_request_id,
            'admin_id': get_admin_id()
        })
        
        if not payment:
            return jsonify({'error': 'Payment not found'}), 404
            
        return jsonify({
            'status': payment['status'],
            'tier': payment.get('tier'),
            'payment_type': payment.get('payment_type')
        })
        
    except Exception as e:
        app.logger.error(f"Payment status check error: {e}")
        return jsonify({'error': 'Failed to check status'}), 500

@app.route('/toggle_auto_renew', methods=['POST'])
@login_required
def toggle_auto_renew():
    """Toggle auto-renewal for monthly subscriptions"""
    try:
        admin_id = get_admin_id()
        admin = mongo.db.admins.find_one({"_id": admin_id})
        
        if admin.get('subscription_type') != 'monthly':
            return jsonify({'error': 'Auto-renewal only applies to monthly subscriptions'}), 400
            
        new_status = not admin.get('auto_renew', True)
        
        mongo.db.admins.update_one(
            {"_id": admin_id},
            {"$set": {"auto_renew": new_status}}
        )
        
        return jsonify({
            'success': True,
            'auto_renew': new_status,
            'message': f'Auto-renewal {"enabled" if new_status else "disabled"}'
        })
        
    except Exception as e:
        app.logger.error(f"Toggle auto-renew error: {e}")
        return jsonify({'error': 'Failed to update auto-renewal'}), 500

def check_subscription_expiry():
    """Check for expiring subscriptions and send reminders"""
    try:
        # Find subscriptions expiring in 3 days
        expiry_date = datetime.now() + timedelta(days=3)
        expiring_admins = mongo.db.admins.find({
            "subscription_end_date": {"$lte": expiry_date},
            "subscription_status": "active",
            "subscription_tier": {"$ne": "free"}
        })
        
        for admin in expiring_admins:
            # Send SMS reminder
            if admin.get('phone'):
                message = f"Your {SUBSCRIPTION_TIERS[admin['subscription_tier']]['name']} subscription expires soon. Renew at app.waterbilling.com/subscription"
                send_message(admin['phone'], message)
                
        # Downgrade expired subscriptions
        mongo.db.admins.update_many(
            {
                "subscription_end_date": {"$lt": datetime.now()},
                "subscription_status": "active"
            },
            {"$set": {
                "subscription_status": "expired",
                "subscription_tier": "free"
            }}
        )
        
    except Exception as e:
        app.logger.error(f"Error checking subscriptions: {e}")


# Call this function when the app starts
if mongo:
    with app.app_context():
        initialize_houses_collection()
        migrate_existing_data()
        migrate_existing_readings_to_payments()
        initialize_subscriptions()
        initialize_subscription_records()
    app.logger.info("Houses collection initialized successfully")



def get_admin_id():
    """Get admin ID from session with validation."""
    try:
        return ObjectId(session['admin_id'])
    except (KeyError, TypeError):
        raise ValueError("Invalid admin session")



# Add to your initialization block
if mongo:
    with app.app_context():
        initialize_houses_collection()
        migrate_existing_data()
        migrate_existing_readings_to_payments()
        initialize_subscriptions()  # Add this line

# # 50 per hour
def send_message(recipient, message, sender=None, retries=3):
    if not TALKSASA_API_KEY:
        return {"error": "SMS service not configured"}

    # Always use the environment variable for sender ID
    sender = TALKSASA_SENDER_ID

    # TalkSasa API endpoint
    url = "https://bulksms.talksasa.com/api/v3/sms/send"
    
    # Add debug logging
    app.logger.info(f"Sending SMS to {recipient} with sender {sender}")
    
    attempt = 0
    last_error = None
    while attempt < retries:
        try:
            # Create headers with Bearer token authentication
            headers = {
                "Authorization": f"Bearer {TALKSASA_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            # Create payload according to API documentation
            payload = {
                "recipient": recipient,
                "sender_id": sender,
                "type": "plain",
                "message": message
            }
            
            # Log the request details (excluding sensitive info)
            app.logger.info(f"Request URL: {url}")
            app.logger.info(f"Request payload: {payload}")
            
            # Send request with proper headers
            response = requests.post(url, json=payload, headers=headers)
            
            # Log the response
            app.logger.info(f"Response status: {response.status_code}")
            app.logger.info(f"Response text: {response.text[:200]}")  # Log first 200 chars to avoid encoding issues
            
            # Check if response is valid JSON
            if response.text.strip():
                try:
                    response_data = response.json()
                    if response.status_code == 200 and response_data.get("status") == "success":
                        return response_data
                    else:
                        last_error = response_data.get("message", "Unknown error")
                except ValueError:
                    last_error = f"Invalid JSON response: {response.text[:100]}..."
            else:
                last_error = "Empty response from API"
                
            app.logger.error(f"Error sending message on attempt {attempt + 1}: {last_error}")
        except Exception as e:
            last_error = str(e)
            app.logger.error(f"Error sending message on attempt {attempt + 1}: {e}")
        
        attempt += 1
    
    return {"error": str(last_error)}

# Enhanced phone number formatter with regex validation
def format_phone_number(phone):
    """Format and validate phone number."""
    phone = re.sub(r'\D', '', phone)  # Remove non-digits
    
    if phone.startswith('254'):
        phone = '+' + phone
    elif phone.startswith('0'):
        phone = '+254' + phone[1:]
    elif phone.startswith('7') or phone.startswith('1'):
        phone = '+254' + phone
    else:
        raise ValueError("Invalid phone number format")
    
    if len(phone) != 13:
        raise ValueError("Phone number must be 13 digits including country code")
    
    return phone  # Add this return statement
    

def get_rate_per_unit(admin_id):
    """Get rate per unit for admin with fallback."""
    sms_config = mongo.db.sms_config.find_one({"admin_id": admin_id})
    if sms_config:
        return sms_config.get('rate_per_unit', RATE_PER_UNIT)
    
    admin = mongo.db.admins.find_one({"_id": admin_id})
    return admin.get('rate_per_unit', RATE_PER_UNIT) if admin else RATE_PER_UNIT



def create_payment_record(admin_id, tenant_id, house_id, bill_amount, reading_id=None, month_year=None, bill_type=None):
    """Create a new payment record when a water reading is recorded"""
    try:
        current_time = datetime.now()  # Full timestamp

        payment_data = {
            'admin_id': ObjectId(admin_id),
            'tenant_id': ObjectId(tenant_id),
            'house_id': ObjectId(house_id),
            'bill_amount': float(bill_amount),
            'amount_paid': 0.0,
            'payment_status': 'unpaid',
            'due_date': datetime.now() + timedelta(days=30),  # 30 days to pay
            'month_year': month_year,
            'reading_id': ObjectId(reading_id),
            'bill_type': bill_type,
            'last_payment_date': None,
            'last_payment_method': None,
            'notes': '',
            'created_at': current_time,
            'updated_at': current_time
        }
        
        result = mongo.db.payments.insert_one(payment_data)
        cache.delete_memoized(get_billing_summary, admin_id)
        return result.inserted_id
    except Exception as e:
        app.logger.error(f"Error creating payment record: {str(e)}")
        return None

def get_unpaid_bills(admin_id, tenant_id=None, bill_type=None):
    """Get all unpaid bills for admin or specific tenant with optional bill_type filter"""
    try:
        query = {
            'admin_id': ObjectId(admin_id),
            'payment_status': {'$in': ['unpaid', 'partial']}
        }
        
        if tenant_id:
            query['tenant_id'] = ObjectId(tenant_id)
            
        if bill_type:
            query['bill_type'] = bill_type
            
        unpaid_bills = list(mongo.db.payments.find(query).sort('due_date', 1))
        return unpaid_bills
    except Exception as e:
        app.logger.error(f"Error fetching unpaid bills: {str(e)}")
        return []
def get_unpaid_bills_paginated(admin_id, page=1, per_page=10, filter_status=None, search_term=None, bill_type=None):
    """Get paginated unpaid bills with optional filtering and bill_type"""
    try:
        # Start with basic query
        query = {'admin_id': ObjectId(admin_id)}
        
        # Add bill_type filter if specified
        if bill_type:
            query['bill_type'] = bill_type
        
        # Add payment status filter
        if filter_status == 'all':
            pass  # No additional filter
        elif filter_status == 'unpaid':
            query['payment_status'] = 'unpaid'
        elif filter_status == 'partial':
            query['payment_status'] = 'partial'
        elif filter_status == 'paid':
            query['payment_status'] = 'paid'
        else:  # Default to showing unpaid and partial
            query['payment_status'] = {'$in': ['unpaid', 'partial']}
        
        # Add search term if provided
        if search_term:
            # Create regex pattern for case-insensitive search
            escaped_query = re.escape(search_term.strip())
            regex_pattern = {"$regex": escaped_query, "$options": "i"}
            
            # Use aggregation pipeline for search across related collections
            pipeline = [
                {'$match': query},
                {'$lookup': {
                    'from': 'tenants',
                    'localField': 'tenant_id',
                    'foreignField': '_id',
                    'as': 'tenant_info'
                }},
                {'$lookup': {
                    'from': 'houses',
                    'localField': 'house_id',
                    'foreignField': '_id',
                    'as': 'house_info'
                }},
                {'$match': {
                    '$or': [
                        {'tenant_info.name': regex_pattern},
                        {'house_info.house_number': regex_pattern}
                    ]
                }},
                {'$sort': {'due_date': 1}},
                {'$skip': (page - 1) * per_page},
                {'$limit': per_page}
            ]
            
            # Count pipeline for total
            count_pipeline = [
                {'$match': query},
                {'$lookup': {
                    'from': 'tenants',
                    'localField': 'tenant_id',
                    'foreignField': '_id',
                    'as': 'tenant_info'
                }},
                {'$lookup': {
                    'from': 'houses',
                    'localField': 'house_id',
                    'foreignField': '_id',
                    'as': 'house_info'
                }},
                {'$match': {
                    '$or': [
                        {'tenant_info.name': regex_pattern},
                        {'house_info.house_number': regex_pattern}
                    ]
                }},
                {'$count': 'total'}
            ]
            
            bills = list(mongo.db.payments.aggregate(pipeline))
            count_result = list(mongo.db.payments.aggregate(count_pipeline))
            total_count = count_result[0]['total'] if count_result else 0
            
        else:
            # Calculate skip value based on page and per_page
            skip = (page - 1) * per_page
            
            # Get total count for pagination
            total_count = mongo.db.payments.count_documents(query)
            
            # Get paginated results
            bills = list(mongo.db.payments.find(query)
                        .sort('due_date', 1)
                        .skip(skip)
                        .limit(per_page))
        
        # Calculate total pages
        total_pages = (total_count + per_page - 1) // per_page  # Ceiling division
        
        return {
            'bills': bills,
            'page': page,
            'per_page': per_page,
            'total_count': total_count,
            'total_pages': total_pages
        }
    except Exception as e:
        app.logger.error(f"Error fetching paginated bills: {str(e)}")
        return {'bills': [], 'page': 1, 'per_page': per_page, 'total_count': 0, 'total_pages': 0}

def get_unpaid_bills_with_aggregation(admin_id, tenant_id=None):
    """Get unpaid bills with outstanding amounts calculated in MongoDB"""
    try:
        match_stage = {
            'admin_id': ObjectId(admin_id),
            'payment_status': {'$in': ['unpaid', 'partial']}
        }
        
        if tenant_id:
            match_stage['tenant_id'] = ObjectId(tenant_id)
            
        pipeline = [
            {'$match': match_stage},
            {'$addFields': {
                'outstanding_amount': {
                    '$subtract': ['$bill_amount', {'$ifNull': ['$amount_paid', 0]}]
                }
            }},
            {'$sort': {'due_date': 1}},
            {'$lookup': {
                'from': 'tenants',
                'localField': 'tenant_id',
                'foreignField': '_id',
                'as': 'tenant_info'
            }},
            {'$lookup': {
                'from': 'houses',
                'localField': 'house_id',
                'foreignField': '_id',
                'as': 'house_info'
            }},
            {'$addFields': {
                'tenant_name': {'$arrayElemAt': ['$tenant_info.name', 0]},
                'house_number': {'$arrayElemAt': ['$house_info.house_number', 0]}
            }},
            {'$project': {
                'tenant_info': 0,
                'house_info': 0
            }}
        ]
        
        return list(mongo.db.payments.aggregate(pipeline))
    except Exception as e:
        app.logger.error(f"Error in unpaid bills aggregation: {str(e)}")
        return []

def update_payment_status(payment_id, amount_paid, payment_method, notes=""):
    """Update payment status when payment is made"""
    try:
        payment = mongo.db.payments.find_one({'_id': ObjectId(payment_id)})
        if not payment:
            return False
            
        new_total_paid = payment.get('amount_paid', 0) + float(amount_paid)
        bill_amount = payment['bill_amount']
        
        if new_total_paid >= bill_amount:
            status = 'paid'
        elif new_total_paid > 0:
            status = 'partial'
        else:
            status = 'unpaid'
            
        update_data = {
            'amount_paid': new_total_paid,
            'payment_status': status,
            'last_payment_date': datetime.now(),
            'last_payment_method': payment_method,
            'notes': notes,
            'updated_at': datetime.now()
        }
        
        result = mongo.db.payments.update_one(
            {'_id': ObjectId(payment_id)},
            {'$set': update_data}
        )
        
        return result.modified_count > 0
    except Exception as e:
        app.logger.error(f"Error updating payment: {str(e)}")
        return False

def calculate_total_arrears(admin_id, tenant_id, bill_type=None, exclude_current_month=None):
    """Calculate total arrears for a tenant excluding current month's bill"""
    try:
        # Ensure ObjectIds
        if not isinstance(admin_id, ObjectId):
            admin_id = ObjectId(admin_id)
        if not isinstance(tenant_id, ObjectId):
            tenant_id = ObjectId(tenant_id)
            
        # Build query to exclude fully paid bills
        query = {
            'admin_id': admin_id,
            'tenant_id': tenant_id,
            'payment_status': {'$in': ['unpaid', 'partial']}
        }
        
        # Add bill_type filter if specified
        if bill_type:
            query['bill_type'] = bill_type
            
        # Exclude current month's bill from arrears calculation
        if exclude_current_month:
            # Ensure the format is YYYY-MM
            if isinstance(exclude_current_month, datetime):
                exclude_current_month = exclude_current_month.strftime('%Y-%m')
            query['month_year'] = {'$ne': exclude_current_month}
            
            # Log for debugging
            app.logger.info(f"Calculating arrears excluding month: {exclude_current_month}")
        
        # Get unpaid/partial bills (excluding current month)
        unpaid_bills = list(mongo.db.payments.find(query))
        total_arrears = 0.0
        
        # Log for debugging
        app.logger.info(f"Found {len(unpaid_bills)} unpaid bills for tenant {tenant_id} (excluding current month: {exclude_current_month})")
        
        # Calculate outstanding amounts with improved precision
        for bill in unpaid_bills:
            bill_amount = float(bill.get('bill_amount', 0))
            amount_paid = float(bill.get('amount_paid', 0))
            outstanding = bill_amount - amount_paid
            
            # Only count positive outstanding amounts with precision threshold
            if outstanding > 0.01:
                total_arrears += outstanding
                # Log each bill being counted
                app.logger.debug(f"Bill {bill['_id']} - Month: {bill.get('month_year')} - Outstanding: {outstanding}")
        
        # Round to 2 decimal places for currency precision
        return round(total_arrears, 2)
        
    except Exception as e:
        app.logger.error(f"Error calculating arrears for tenant {tenant_id}: {str(e)}")
        return 0.0


def get_all_bills(admin_id, tenant_id=None):
    """Get all bills for admin or specific tenant regardless of payment status"""
    try:
        query = {
            'admin_id': ObjectId(admin_id)
        }
        
        if tenant_id:
            query['tenant_id'] = ObjectId(tenant_id)
            
        all_bills = list(mongo.db.payments.find(query))
        return all_bills
    except Exception as e:
        app.logger.error(f"Error fetching all bills: {str(e)}")
        return []

def build_tenant_search_query(admin_id, search_query="", search_type="all"):
    """Build MongoDB query for tenant search."""
    base_query = {"admin_id": admin_id}
    
    if not search_query:
        return base_query
    
    escaped_query = re.escape(search_query.strip())
    regex_pattern = {"$regex": escaped_query, "$options": "i"}
    
    search_fields = {
        'name': {"name": regex_pattern},
        'house': {"house_number": regex_pattern},
        'phone': {"phone": regex_pattern},
        'all': {
            "$or": [
                {"name": regex_pattern},
                {"house_number": regex_pattern},
                {"phone": regex_pattern}
            ]
        }
    }
    
    if search_type in search_fields and search_type != 'all':
        base_query.update(search_fields[search_type])
    else:
        base_query.update(search_fields['all'])
    
    return base_query

@app.after_request
def add_security_headers(response):
    # Allow self, cdn.jsdelivr.net for scripts, and add nonce for inline scripts if needed
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://via.placeholder.com; style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; font-src 'self' https://cdn.jsdelivr.net data:;"
    return response
    
# Routes
# Add a route to trigger migration (remove after running once)
@app.route('/migrate_payments')
@login_required
def migrate_payments():
    """Trigger payment migration - remove this route after running once"""
    if migrate_existing_readings_to_payments():
        flash('Migration completed successfully', 'success')
    else:
        flash('Migration failed', 'error')
    return redirect(url_for('dashboard'))

@app.route('/')
def index():
    #return redirect(url_for('login'))
    return render_template('home.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'admin_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not username or not password:
            flash('Username and password are required', 'danger')
            return render_template('login.html')

        admin = mongo.db.admins.find_one({"username": username})
        if admin and check_password_hash(admin['password'], password):
            session['admin_id'] = str(admin['_id'])
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials', 'danger')

    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if 'admin_id' in session:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        try:
            # Get form data with proper error handling
            name = request.form.get('name', '').strip()
            password = request.form.get('password', '')
            confirm_password = request.form.get('confirm-password', '')
            payment_method = request.form.get('payment_method', '').strip()
            
            # Handle cost conversion with error handling
            cost_str = request.form.get('cost', '').strip()
            try:
                cost = float(cost_str) if cost_str else None
            except (ValueError, TypeError):
                flash('Please enter a valid cost per unit', 'danger')
                return render_template('signup.html')
            
            # Basic validation
            if not all([name, password, confirm_password, cost, payment_method]):
                flash('All fields are required', 'danger')
                return render_template('signup.html')
                
            if password != confirm_password:
                flash('Passwords do not match', 'danger')
                return render_template('signup.html')
            
            if cost <= 0:
                flash('Cost per unit must be greater than 0', 'danger')
                return render_template('signup.html')
            
            # Initialize payment details
            payment_details = {}
            
            # Validate payment method and related fields
            if payment_method == 'till':
                till = request.form.get('till', '').strip()
                if not till or not re.match(r'^\d{6}$', till):
                    flash('Till number must be exactly 6 digits', 'danger')
                    return render_template('signup.html')
                
                # Check if till already exists
                existing_admin = mongo.db.admins.find_one({"till": till})
                if existing_admin:
                    flash('An account with this till number already exists', 'danger')
                    return render_template('signup.html')
                    
                payment_details = {
                    'payment_method': 'till',
                    'till': till
                }
                
            elif payment_method == 'paybill':
                business_number = request.form.get('business_number', '').strip()
                account_name = request.form.get('account_name', '').strip()
                
                if not business_number or not business_number.isdigit():
                    flash('Business number must be a valid number', 'danger')
                    return render_template('signup.html')
                    
                if not account_name:
                    flash('Account name is required', 'danger')
                    return render_template('signup.html')
                
                # Check if business number already exists
                existing_admin = mongo.db.admins.find_one({"business_number": business_number})
                if existing_admin:
                    flash('An account with this business number already exists', 'danger')
                    return render_template('signup.html')
                    
                payment_details = {
                    'payment_method': 'paybill',
                    'business_number': business_number,
                    'account_name': account_name
                }
            else:
                flash('Please select a valid payment method', 'danger')
                return render_template('signup.html')
            
            # Create new admin with isolated data structure
            hashed_password = generate_password_hash(password)
            new_admin = {
                "name": name,
                "username": name,  
                "password": hashed_password,
                "rate_per_unit": cost,
                "created_at": datetime.now(),
                "tenants": [],  # Empty array to store this admin's tenants
                "houses": [],   # Empty array to store this admin's houses
                "readings": [],  # Empty array to store this admin's readings
                **payment_details  # Add payment details to admin document
            }
            
            # Insert the new admin
            result = mongo.db.admins.insert_one(new_admin)
            
            if result.inserted_id:
                # Create SMS config for this admin
                sms_config = {
                    "admin_id": result.inserted_id,
                    "rate_per_unit": cost,
                    "created_at": datetime.now()
                }
                mongo.db.sms_config.insert_one(sms_config)
                
                flash('Account created successfully! You can now log in.', 'success')
                return redirect(url_for('login'))
            else:
                flash('Failed to create account. Please try again.', 'danger')
                return render_template('signup.html')
                
        except Exception as e:
            app.logger.error(f"Error creating account: {str(e)}")
            flash('An error occurred while creating your account. Please try again.', 'danger')
            return render_template('signup.html')
            
    return render_template('signup.html')
    
@app.route('/logout')
def logout():
    session.pop('admin_id', None)
    flash('You have been logged out', 'danger')
    return redirect(url_for('login'))

# Optimized route handlers
@app.route('/dashboard')
@login_required
def dashboard():
    """Main dashboard with optimized queries and error handling."""
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    if mongo is None:
        flash('Database connection error. Please try again later.', 'danger')
        return render_template('error.html', error_message="Database unavailable")
    
    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', DEFAULT_PER_PAGE, type=int), 100)  # Limit max per_page
    search_query = request.args.get('search', '').strip()
    search_type = request.args.get('search_type', 'all')
    
    # Build query and get tenants with single database call
    query = build_tenant_search_query(admin_id, search_query, search_type)
    skip = (page - 1) * per_page
    
    # Use aggregation pipeline for better performance
    pipeline = [
        {"$match": query},
        {"$facet": {
            "tenants": [
                {"$sort": {"name": 1}},
                {"$skip": skip},
                {"$limit": per_page}
            ],
            "total": [{"$count": "count"}]
        }}
    ]
    
    result = list(mongo.db.tenants.aggregate(pipeline))[0]
    tenants = result['tenants']
    total_count = result['total'][0]['count'] if result['total'] else 0
    
    # Add string ID for template compatibility
    for tenant in tenants:
        tenant['id'] = str(tenant['_id'])
    
    # Create pagination object
    total_pages = (total_count + per_page - 1) // per_page
    pagination = {
        'page': page,
        'per_page': per_page,
        'total': total_count,
        'pages': total_pages,
        'has_prev': page > 1,
        'has_next': page < total_pages,
        'items': tenants
    }
    
    # Get recent readings with optimized aggregation
    readings_pipeline = [
        {"$match": {"admin_id": admin_id}},
        {"$lookup": {
            "from": "tenants",
            "localField": "tenant_id",
            "foreignField": "_id",
            "as": "tenant_info"
        }},
        {"$unwind": "$tenant_info"},
        {"$sort": {"date_recorded": -1}},
        {"$limit": 20},
        {"$project": {
            "_id": 1,
            "current_reading": 1,
            "previous_reading": 1,
            "usage": 1,
            "bill_amount": 1,
            "date_recorded": 1,
            "sms_status": 1,
            "tenant_info": {
                "_id": 1,
                "name": 1,
                "house_number": 1
            }
        }}
    ]
    
    readings = list(mongo.db.meter_readings.aggregate(readings_pipeline))
    
    # Format readings for template
    formatted_readings = []
    for reading in readings:
        reading['tenant_info']['id'] = str(reading['tenant_info']['_id'])
        formatted_readings.append((reading, reading['tenant_info']))
    readings_count = len(formatted_readings)

    current_billing_period = formatted_readings[0][0]['date_recorded'].strftime('%b %Y') if formatted_readings else 'N/A'

    # Get rate per unit
    rate_per_unit = get_rate_per_unit(admin_id)
    # Add subscription info
    admin = mongo.db.admins.find_one({"_id": admin_id})
    tier = admin.get('subscription_tier', 'starter')
    tier_config = SUBSCRIPTION_TIERS.get(tier, SUBSCRIPTION_TIERS['starter'])
    
    return render_template(
        'dashboard.html',
        tenants=tenants,
        pagination=pagination,
        readings=formatted_readings,
        current_billing_period=current_billing_period,
        readings_count=readings_count,  # Add this line
        search_query=search_query,
        search_type=search_type,
        rate_per_unit=rate_per_unit,
        tenant_count=total_count,
        max_tenants=tier_config['max_tenants'],
        subscription_tier_name=tier_config['name']
    )

@app.route('/tenant/<tenant_id>')
@login_required
def tenant_details(tenant_id):
    # Convert string ID to ObjectId
    admin_id = get_admin_id()

    tenant_id_obj = ObjectId(tenant_id)
    tenant = mongo.db.tenants.find_one({"_id": tenant_id_obj, "admin_id": admin_id})    
    if not tenant:
        flash('Tenant not found', 'danger')
        return redirect(url_for('dashboard'))

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', DEFAULT_PER_PAGE, type=int)
    skip = (page - 1) * per_page

    # For table display, use descending order
    readings_count = mongo.db.meter_readings.count_documents({"tenant_id": tenant_id_obj, "admin_id": admin_id})    
    readings = list(mongo.db.meter_readings.find(
            {"tenant_id": tenant_id_obj, "admin_id": admin_id}
        ).sort("date_recorded", -1).skip(skip).limit(per_page))
    
    # Create pagination object
    total_pages = (readings_count + per_page - 1) // per_page
    pagination = {
        'page': page,
        'per_page': per_page,
        'total': readings_count,
        'pages': total_pages,
        'has_prev': page > 1,
        'has_next': page < total_pages,
        'items': readings
    }

    # For chart data, fetch readings in chronological order
    chart_readings = list(mongo.db.meter_readings.find(
            {"tenant_id": tenant_id_obj, "admin_id": admin_id}
        ).sort("date_recorded", 1))

    labels = [r['date_recorded'].strftime('%Y-%m-%d') for r in chart_readings]
    usage_data = [r['usage'] for r in chart_readings]
    bill_data = [r['bill_amount'] for r in chart_readings]

    # Get latest reading separately from pagination
    latest_reading = mongo.db.meter_readings.find_one(
        {"tenant_id": tenant_id_obj, "admin_id": admin_id},
        sort=[("date_recorded", -1)]
    )
    
    return render_template(
        'tenant_details.html',
        tenant=tenant,
        readings=readings,
        pagination=pagination,
        labels=labels,
        usage_data=usage_data,
        bill_data=bill_data,
        latest_reading=latest_reading,
        datetime=datetime
    )

@app.route('/api/tenant/<tenant_id>/readings')
@login_required
def tenant_readings_data(tenant_id):
    """Get tenant readings data from meter_readings collection."""
    try:
        admin_id = get_admin_id()
        tenant_id_obj = ObjectId(tenant_id)
        tenant = mongo.db.tenants.find_one({"_id": tenant_id_obj, "admin_id": admin_id})    

        if not tenant:
            return jsonify({"error": "Tenant not found"}), 404
        
        house_number = tenant.get('house_number')
        
        # Get all readings for this tenant from meter_readings
        # Sort by date_recorded descending (newest first)
        tenant_readings = list(mongo.db.meter_readings.find(
            {"tenant_id": tenant_id_obj, "admin_id": admin_id}
        ).sort("date_recorded", -1))
        
        # If no tenant readings, check for house readings by house_number
        if not tenant_readings and house_number:
            house_readings = list(mongo.db.meter_readings.find(
                {"house_number": house_number, "admin_id": admin_id}
            ).sort("date_recorded", -1).limit(1))
            
            if house_readings:
                house_reading = house_readings[0]
                data = [{
                    'date': house_reading['date_recorded'].strftime('%Y-%m-%d'),
                    'time': house_reading['date_recorded'].strftime('%H:%M:%S'),
                    'datetime': house_reading['date_recorded'].strftime('%Y-%m-%d %H:%M:%S'),
                    'timestamp': house_reading['date_recorded'].isoformat(),
                    'previous_reading': house_reading['previous_reading'],
                    'current_reading': house_reading['current_reading'],
                    'usage': house_reading['usage'],
                    'bill_amount': house_reading['bill_amount'],
                    'sms_status': house_reading.get('sms_status', 'not_sent')
                }]
                return jsonify(data)
        
        # Return tenant readings from meter_readings
        data = []
        for reading in tenant_readings:
            data.append({
                'date': reading['date_recorded'].strftime('%Y-%m-%d'),
                'time': reading['date_recorded'].strftime('%H:%M:%S'),
                'datetime': reading['date_recorded'].strftime('%Y-%m-%d %H:%M:%S'),
                'timestamp': reading['date_recorded'].isoformat(),
                'previous_reading': reading['previous_reading'],
                'current_reading': reading['current_reading'],
                'usage': reading['usage'],
                'bill_amount': reading['bill_amount'],
                'sms_status': reading.get('sms_status', 'not_sent')
            })
        
        return jsonify(data)
        
    except Exception as e:
        app.logger.error(f"Error getting tenant readings data: {str(e)}")
        return jsonify({"error": "Failed to fetch readings"}), 500
    
@app.route('/add_tenant', methods=['POST'])
@login_required
@check_subscription_limit('tenant')  # Add this decorator
def add_tenant():
    """Add a new tenant with improved validation."""
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    # Get form data
    name = request.form.get('name', '').strip()
    house_number = request.form.get('house_number', '').strip()
    phone = request.form.get('phone', '').strip()
    
    # Validate inputs
    if not all([name, house_number, phone]):
        flash('All fields are required', 'danger')
        return redirect(url_for('dashboard'))
    
    try:
        # Format phone number
        formatted_phone = format_phone_number(phone)
        
        # Check if phone number already exists for this admin
        existing_tenant = mongo.db.tenants.find_one({
            "phone": formatted_phone,
            "admin_id": admin_id
        })
        
        if existing_tenant:
            flash(f'Phone number already exists for tenant: {existing_tenant["name"]}', 'danger')
            return redirect(url_for('dashboard'))
        
        # Check if house exists and is available for this admin
        house = mongo.db.houses.find_one({
            "house_number": house_number,
            "admin_id": admin_id
        })
        
        if house and house.get("is_occupied"):
            # Get tenant name from house document
            tenant_name = house.get("current_tenant_name", "Unknown")
            
            # If tenant name not in house document, try to find it from tenants collection
            if tenant_name == "Unknown" and "current_tenant_id" in house:
                tenant_doc = mongo.db.tenants.find_one({"_id": house["current_tenant_id"]})
                if tenant_doc:
                    tenant_name = tenant_doc.get("name", "Unknown")
                    
            flash(f'House {house_number} is already occupied by {tenant_name}', 'danger')
            return redirect(url_for('dashboard'))
        
        # Create new tenant
        tenant_id = ObjectId()
        new_tenant = {
            "_id": tenant_id,
            "name": name,
            "phone": formatted_phone,
            "house_number": house_number,
            "admin_id": admin_id,
            "created_at": datetime.now()
        }
        
        # If house exists, store its _id in the tenant document
        if house:
            new_tenant["house_id"] = house["_id"]
        
        # Insert tenant
        mongo.db.tenants.insert_one(new_tenant)
        
        # Create or update house
        if house:
            # Update existing house
            mongo.db.houses.update_one(
                {"_id": house["_id"]},
                {"$set": {
                    "is_occupied": True,
                    "current_tenant_id": tenant_id,
                    "current_tenant_name": name
                }}
            )
        else:
            # Create new house
            house_id = ObjectId()
            new_house = {
                "_id": house_id,
                "house_number": house_number,
                "is_occupied": True,
                "current_tenant_id": tenant_id,
                "current_tenant_name": name,
                "admin_id": admin_id,
                "created_at": datetime.now(),
                "rent": 0
            }
            mongo.db.houses.insert_one(new_house)
            
            # Update tenant with house_id
            mongo.db.tenants.update_one(
                {"_id": tenant_id},
                {"$set": {"house_id": house_id}}
            )
        
        flash('Tenant added successfully', 'success')
        
    except ValueError as e:
        flash(f'Error: {str(e)}', 'danger')
    except Exception as e:
        app.logger.error(f"Error adding tenant: {e}")
        flash('An error occurred while adding the tenant', 'danger')
    
    return redirect(url_for('dashboard'))

@app.route('/houses', methods=['GET'])
@login_required
def houses():
    """Display houses management page with all houses"""
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', DEFAULT_PER_PAGE, type=int), 100)  # Limit max per_page
    search_query = request.args.get('search', '').strip()
    status = request.args.get('status', '').strip()
    
    # Build query for houses
    query = {"admin_id": admin_id}
    if search_query:
        escaped_query = re.escape(search_query)
        query["house_number"] = {"$regex": escaped_query, "$options": "i"}
    
    # Add status filter
    if status == 'occupied':
        query["is_occupied"] = True
    elif status == 'vacant':
        query["is_occupied"] = False
    
    # Use aggregation pipeline for better performance
    pipeline = [
        {"$match": query},
        {"$facet": {
            "houses": [
                {"$sort": {"house_number": 1}},
                {"$skip": (page - 1) * per_page},
                {"$limit": per_page}
            ],
            "total": [{"$count": "count"}]
        }}
    ]
    
    result = list(mongo.db.houses.aggregate(pipeline))[0]
    houses = result['houses']
    total_count = result['total'][0]['count'] if result['total'] else 0
    
    # Add string ID for template compatibility
    for house in houses:
        house['id'] = str(house['_id'])
        # Get tenant info if house is occupied
        if house.get('is_occupied') and house.get('current_tenant_id'):
            tenant = mongo.db.tenants.find_one({"_id": house["current_tenant_id"]})
            if tenant:
                house['tenant_name'] = tenant.get('name', 'Unknown')
                house['tenant_id'] = str(tenant['_id'])
            else:
                house['tenant_name'] = house.get('current_tenant_name', 'Unknown')
                house['tenant_id'] = None
    
    # Create pagination object
    total_pages = (total_count + per_page - 1) // per_page
    pagination = {
        'page': page,
        'per_page': per_page,
        'total': total_count,
        'pages': total_pages,
        'has_prev': page > 1,
        'has_next': page < total_pages,
        'items': houses
    }
    
    return render_template(
        'houses.html',
        houses=houses,
        pagination=pagination,
        search_query=search_query,
        status=status
    )


@app.route('/add_house', methods=['POST'])
@login_required
@check_subscription_limit('house')  # Add this decorator
def add_house():
    """Add a new house with validation."""
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    # Get form data
    house_number = request.form.get('house_number', '').strip()
    rent = request.form.get('rent', '0').strip()
    
    # Validate inputs
    if not house_number:
        flash('House number is required', 'danger')
        return redirect(url_for('houses'))
    
    try:
        # Convert rent to float
        rent = float(rent)
        if rent < 0:
            raise ValueError("Rent cannot be negative")
            
        # Check if house number already exists for this admin
        existing_house = mongo.db.houses.find_one({
            "house_number": house_number,
            "admin_id": admin_id
        })
        
        if existing_house:
            flash(f'House number {house_number} already exists', 'danger')
            return redirect(url_for('houses'))
        
        # Create new house
        house_id = ObjectId()
        new_house = {
            "_id": house_id,
            "house_number": house_number,
            "is_occupied": False,
            "current_tenant_id": None,
            "current_tenant_name": None,
            "admin_id": admin_id,
            "created_at": datetime.now(),
            "rent": rent
        }
        
        # Insert house
        mongo.db.houses.insert_one(new_house)
        
        flash('House added successfully', 'success')
        
    except ValueError as e:
        flash(f'Error: {str(e)}', 'danger')
    except Exception as e:
        app.logger.error(f"Error adding house: {e}")
        flash('An error occurred while adding the house', 'danger')
    
    return redirect(url_for('houses'))

@app.route('/edit_house/<house_id>', methods=['POST'])
@login_required
def edit_house(house_id):
    """Edit house with validation."""
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    # Get form data
    house_number = request.form.get('house_number', '').strip()
    rent = request.form.get('rent', '0').strip()
    
    # Validate inputs
    if not house_number:
        flash('House number is required', 'danger')
        return redirect(url_for('houses'))
    
    try:
        # Convert rent to float
        rent = float(rent)
        if rent < 0:
            raise ValueError("Rent cannot be negative")
            
        house_id_obj = ObjectId(house_id)
        house = mongo.db.houses.find_one({"_id": house_id_obj, "admin_id": admin_id})
        
        if not house:
            flash('House not found', 'danger')
            return redirect(url_for('houses'))
        
        old_house_number = house.get('house_number', '')
        
        # If house number is changing, check if it already exists
        if house_number != old_house_number:
            existing_house = mongo.db.houses.find_one({
                "house_number": house_number,
                "admin_id": admin_id,
                "_id": {"$ne": house_id_obj}
            })
            
            if existing_house:
                flash(f'House number {house_number} already exists', 'danger')
                return redirect(url_for('houses'))
            
            # Update tenant's house_number if house is occupied
            if house.get('is_occupied') and house.get('current_tenant_id'):
                mongo.db.tenants.update_one(
                    {"_id": house["current_tenant_id"]},
                    {"$set": {"house_number": house_number}}
                )
        
        # Update house
        mongo.db.houses.update_one(
            {"_id": house_id_obj},
            {"$set": {
                "house_number": house_number,
                "rent": rent
            }}
        )
        
        flash('House updated successfully', 'success')
        
    except ValueError as e:
        flash(f'Error: {str(e)}', 'danger')
    except Exception as e:
        app.logger.error(f"Error updating house: {e}")
        flash('An error occurred while updating the house', 'danger')
    
    return redirect(url_for('houses'))

@app.route('/delete_house/<house_id>', methods=['POST'])
@login_required
def delete_house(house_id):
    """Delete house if it's not occupied."""
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    try:
        house_id_obj = ObjectId(house_id)
        house = mongo.db.houses.find_one({"_id": house_id_obj, "admin_id": admin_id})
        
        if not house:
            flash('House not found', 'danger')
            return redirect(url_for('houses'))
        
        # Check if house is occupied
        if house.get('is_occupied'):
            flash('Cannot delete an occupied house. Please transfer or remove the tenant first.', 'danger')
            return redirect(url_for('houses'))
        
        # Delete house
        mongo.db.houses.delete_one({"_id": house_id_obj, "admin_id": admin_id})
        
        flash('House deleted successfully', 'success')
        
    except Exception as e:
        app.logger.error(f"Error deleting house: {e}")
        flash('An error occurred while deleting the house', 'danger')
    
    return redirect(url_for('houses'))

@app.route('/assign_tenant/<house_id>', methods=['POST'])
@login_required
def assign_tenant(house_id):
    """Assign a tenant to a house."""
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    # Get form data
    tenant_id = request.form.get('tenant_id', '').strip()
    
    # Validate inputs
    if not tenant_id:
        flash('Tenant is required', 'danger')
        return redirect(url_for('houses'))
    
    try:
        house_id_obj = ObjectId(house_id)
        tenant_id_obj = ObjectId(tenant_id)
        
        house = mongo.db.houses.find_one({"_id": house_id_obj, "admin_id": admin_id})
        tenant = mongo.db.tenants.find_one({"_id": tenant_id_obj, "admin_id": admin_id})
        
        if not house:
            flash('House not found', 'danger')
            return redirect(url_for('houses'))
            
        if not tenant:
            flash('Tenant not found', 'danger')
            return redirect(url_for('houses'))
        
        # Check if house is already occupied
        if house.get('is_occupied'):
            flash('House is already occupied. Please transfer or remove the current tenant first.', 'danger')
            return redirect(url_for('houses'))
        
        # Check if tenant already has a house
        if tenant.get('house_number'):
            old_house = mongo.db.houses.find_one({
                "house_number": tenant["house_number"],
                "admin_id": admin_id
            })
            
            if old_house:
                # Update old house
                mongo.db.houses.update_one(
                    {"_id": old_house["_id"]},
                    {"$set": {
                        "is_occupied": False,
                        "current_tenant_id": None,
                        "current_tenant_name": None
                    }}
                )
        
        # Update house
        mongo.db.houses.update_one(
            {"_id": house_id_obj},
            {"$set": {
                "is_occupied": True,
                "current_tenant_id": tenant_id_obj,
                "current_tenant_name": tenant["name"]
            }}
        )
        
        # Update tenant
        mongo.db.tenants.update_one(
            {"_id": tenant_id_obj},
            {"$set": {
                "house_number": house["house_number"],
                "house_id": house_id_obj
            }}
        )
        
        flash(f'Tenant {tenant["name"]} assigned to house {house["house_number"]} successfully', 'success')
        
    except Exception as e:
        app.logger.error(f"Error assigning tenant: {e}")
        flash('An error occurred while assigning the tenant', 'danger')
    
    return redirect(url_for('houses'))

@app.route('/transfer_tenant/<tenant_id>', methods=['GET', 'POST'])
@login_required
def transfer_tenant(tenant_id):
    try:
        tenant_id_obj = ObjectId(tenant_id)
        admin_id = get_admin_id()
        
        # Get tenant details
        tenant = mongo.db.tenants.find_one({"_id": tenant_id_obj, "admin_id": admin_id})
        if not tenant:
            flash('Tenant not found', 'danger')
            return redirect(url_for('dashboard'))
            
        tenant_name = tenant.get('name', 'Unknown')
        current_house = tenant.get('house_number', 'Unknown')
        
        # Get all available houses for transfer from houses collection
        # Available houses are those that exist and are not occupied
        available_houses = [h['house_number'] for h in mongo.db.houses.find(
            {"is_occupied": False, "admin_id": admin_id}
        )]
        
        # Also include houses that don't exist in the houses collection yet
        # This is optional and depends on how you want to handle new houses
        unpaid_bills = get_unpaid_bills(admin_id, tenant_id_obj)

        if unpaid_bills:
            total_arrears = calculate_total_arrears(admin_id, tenant_id_obj)
            flash(f'Cannot transfer tenant with unpaid bills. Outstanding amount: KSh{total_arrears:.2f}. Please settle all bills before transferring.', 'danger')
            return redirect(url_for('dashboard'))
        else:
            if request.method == 'POST':
                new_house = request.form.get('new_house')
                
                if not new_house:
                    flash('Please select a new house', 'danger')
                    return render_template('transfer_tenant.html', tenant=tenant, houses=available_houses)
                    
                if new_house == current_house:
                    flash('New house cannot be the same as current house', 'warning')
                    return render_template('transfer_tenant.html', tenant=tenant, houses=available_houses)
                
                # Check if the new house exists in houses collection
                new_house_doc = mongo.db.houses.find_one({
                    "house_number": new_house, 
                    "admin_id": admin_id
                })
                
                # If new house exists and is occupied, prevent transfer
                if new_house_doc and new_house_doc.get("is_occupied"):
                    # Get tenant name from house document
                    occupied_tenant_name = new_house_doc.get("current_tenant_name", "Unknown")
                    
                    # If tenant name not in house document, try to find it from tenants collection
                    if occupied_tenant_name == "Unknown" and "current_tenant_id" in new_house_doc:
                        tenant_doc = mongo.db.tenants.find_one({"_id": new_house_doc["current_tenant_id"]})
                        if tenant_doc:
                            occupied_tenant_name = tenant_doc.get("name", "Unknown")
                    
                    flash(f'House {new_house} is already occupied by {occupied_tenant_name}. Please select a different house.', 'danger')
                    return render_template('transfer_tenant.html', tenant=tenant, houses=available_houses)
                
                # Get current house document
                current_house_doc = mongo.db.houses.find_one({
                    "house_number": current_house,
                    "admin_id": admin_id
                })
                
                # Get all readings for this tenant
                readings = list(mongo.db.meter_readings.find({"tenant_id": tenant_id_obj, "admin_id": admin_id}))
                
                # Create a house_readings collection if it doesn't exist
                """if 'house_readings' not in mongo.db.list_collection_names():
                    mongo.db.create_collection('house_readings')"""
                
                # For each reading, store it in the house_readings collection associated with the old house
                for reading in readings:
                    # Check if this reading already exists in house_readings
                    existing = mongo.db.meter_readings.find_one({
                        "house_id": current_house_doc["_id"],
                        "house_number": current_house,
                        "date_recorded": reading['date_recorded']
                    })
                    
                    if not existing:
                        # Create a new house reading record for the old house
                        house_reading = {
                            "house_number": current_house,
                            "previous_reading": reading['previous_reading'],
                            "current_reading": reading['current_reading'],
                            "usage": reading['usage'],
                            "bill_amount": reading['bill_amount'],
                            "date_recorded": reading['date_recorded'],
                            "created_from_tenant": tenant_name,
                            "admin_id": admin_id
                        }
                        
                        # Add house_id if available
                        if current_house_doc:
                            house_reading["house_id"] = current_house_doc["_id"]
                        
                        mongo.db.meter_readings.insert_one(house_reading)
                
                # Update old house to mark as unoccupied
                if current_house_doc:
                    mongo.db.houses.update_one(
                        {"_id": current_house_doc["_id"]},
                        {"$set": {
                            "is_occupied": False,
                            "current_tenant_id": None,
                            "current_tenant_name": None
                        }}
                    )
                
                # If new house doesn't exist in houses collection, create it
                if not new_house_doc:
                    new_house_id = ObjectId()
                    new_house_doc = {
                        "_id": new_house_id,
                        "house_number": new_house,
                        "is_occupied": True,
                        "current_tenant_id": tenant_id_obj,
                        "current_tenant_name": tenant_name,
                        "admin_id": admin_id,
                        "created_at": datetime.now(),
                        "rent": 0
                    }
                    mongo.db.houses.insert_one(new_house_doc)
                else:
                    new_house_id = new_house_doc["_id"]
                    # Update new house to mark as occupied
                    mongo.db.houses.update_one(
                        {"_id": new_house_id},
                        {"$set": {
                            "is_occupied": True,
                            "current_tenant_id": tenant_id_obj,
                            "current_tenant_name": tenant_name
                        }}
                    )
                
                # Now update the tenant's house number and house_id
                mongo.db.tenants.update_one(
                    {"_id": tenant_id_obj},
                    {"$set": {
                        "house_number": new_house,
                        "house_id": new_house_id
                    }}
                )
                
                # Clear the tenant's readings since they're now in a new house
                mongo.db.meter_readings.delete_many({"tenant_id": tenant_id_obj})
                
                flash(f'Tenant "{tenant_name}" has been transferred from house {current_house} to house {new_house}. Reading history for both houses has been preserved.', 'success')
                return redirect(url_for('dashboard'))
                
        return render_template('transfer_tenant.html', tenant=tenant, houses=available_houses)
        
    except Exception as e:
        app.logger.error(f"Error transferring tenant: {e}")
        flash(f'Error: {str(e)}', 'danger')
        return redirect(url_for('dashboard'))

def get_last_house_reading(house_number, admin_id):
    """Get the last reading for a specific house number from meter_readings."""
    if not house_number:
        return None
        
    last_reading = mongo.db.meter_readings.find_one(
        {"house_number": house_number, "admin_id": admin_id},
        sort=[("date_recorded", -1)]
    )
    
    return last_reading

#replace with a function to handle bulk reading uploads
@app.route('/record_reading', methods=['POST'])
@login_required
def record_reading():
    """Record water reading with improved validation and error handling."""
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    # Get and validate form data
    tenant_id = request.form.get('tenant_id', '').strip()
    
    try:
        # Get current reading from form
        current_reading = float(request.form.get('current_reading', 0))
        
        # We'll get previous reading from the database based on house number
        previous_reading = float(request.form.get('previous_reading', 0))
    except (ValueError, TypeError):
        flash('Invalid reading values. Please enter valid numbers.', 'danger')
        return redirect(url_for('dashboard'))
    
    # Validation
    if not tenant_id:
        flash('Tenant ID is required', 'danger')
        return redirect(url_for('dashboard'))
    
    if current_reading < 0:
        flash('Readings cannot be negative', 'danger')
        return redirect(url_for('dashboard'))
    
    try:
        tenant_id_obj = ObjectId(tenant_id)
        tenant = mongo.db.tenants.find_one({"_id": tenant_id_obj, "admin_id": admin_id})
        
        if not tenant:
            flash('Tenant not found', 'danger')
            return redirect(url_for('dashboard'))
        
        house_number = tenant.get('house_number')
        if not house_number:
            flash('Tenant has no associated house number', 'danger')
            return redirect(url_for('dashboard'))
        
        # Get the house document to retrieve house_id
        house = mongo.db.houses.find_one({"house_number": house_number, "admin_id": admin_id})
        house_id = house.get('_id') if house else None
        
        # Get the latest reading for this house number (regardless of tenant)
        latest_house_reading = get_last_house_reading(house_number, admin_id)
        
        # If we have a previous reading for this house, use it
        if latest_house_reading:
            previous_reading = latest_house_reading.get('current_reading', 0)
        
        # Validate that current reading is greater than previous
        if current_reading <= previous_reading:
            flash('Current reading must be greater than previous reading', 'danger')
            return redirect(url_for('dashboard'))
        
        # Calculate billing
        usage = current_reading - previous_reading
        rate_per_unit = get_rate_per_unit(admin_id)
        bill_amount = usage * rate_per_unit
        
        # Create reading records
        current_time = datetime.now()  # This includes full datetime with microseconds
        
        # Create tenant reading record (for billing purposes)
        reading_data = {
            "tenant_id": tenant_id_obj,
            "house_number": house_number,
            "house_id": house_id,
            "previous_reading": previous_reading,
            "current_reading": current_reading,
            "usage": usage,
            "bill_amount": bill_amount,
            "date_recorded": current_time,
            "sms_status": "pending",
            "admin_id": admin_id,
            "tenant_name": tenant['name'],
            "current_tenant_id": tenant_id_obj,
            "reading_type": "standard_billing",
            "source_collection": "meter_readings"
        }
    
        # Single insert to meter_readings
        result = mongo.db.meter_readings.insert_one(reading_data)
        reading_id = result.inserted_id
    
        
        # Create payment record for this bill - FIXED
        month_year = current_time.strftime('%Y-%m')
        payment_id = create_payment_record(
            admin_id=admin_id,  # Fixed: use admin_id instead of session['admin_id']
            tenant_id=tenant_id_obj,  # Fixed: use tenant_id_obj
            house_id=house_id,  # Fixed: use house_id instead of house_data['_id']
            bill_amount=bill_amount,
            reading_id=reading_id,  # Fixed: use reading_id instead of reading_result.inserted_id
            month_year=month_year
        )

        if payment_id:
            app.logger.info(f"Payment record created with ID: {payment_id}")
        else:
            app.logger.error("Failed to create payment record")
        
        # Create house reading record (for historical tracking)

        
        # Prepare and send SMS with arrears information - ENHANCED
        admin = mongo.db.admins.find_one({"_id": admin_id})
        admin_name = admin.get('name', 'Your Landlord') if admin else 'Your Landlord'
        admin_phone = admin.get('phone', 'N/A') if admin else 'N/A'
        
        # Get payment details based on admin's payment method
        payment_info = ""
        if admin:
            if admin.get('payment_method') == 'till':
                till_number = admin.get('till', 'N/A')
                payment_info = f"Pay via Till: {till_number}"
            elif admin.get('payment_method') == 'paybill':
                business_number = admin.get('business_number', 'N/A')
                account_name = admin.get('account_name', 'N/A')
                payment_info = f"Pay via PayBill: {business_number}, Account: {account_name}"
            else:
                # Fallback for existing accounts without payment_method
                till_number = admin.get('till', 'N/A')
                payment_info = f"Pay via Till: {till_number}"
        
            # In record_reading function around line 1972
            current_month_year = get_current_month_year()
            total_arrears = calculate_total_arrears(admin_id, tenant_id_obj, bill_type='water', exclude_current_month=current_month_year)
            if total_arrears > 1:
                message = (
                    f"Water Bill Alert: {tenant['name']}, House {house_number}. "
                    f"Current bill: KES {bill_amount:.2f}. "
                    f"Outstanding arrears: KES {total_arrears:.2f}. "
                    f"{payment_info} "
                    f"From {admin_name} - {admin_phone}"
                )
            else:
                message = (
                    f"Water Bill Alert: {tenant['name']}, House {house_number}.\n "
                    f"Current bill: KES {bill_amount:.2f}. \n"
                    f"{payment_info}\n"
                    f" From {admin_name} - {admin_phone}"
                )
        
        # Send SMS (assuming send_message function exists)
        try:
            response = send_message(tenant['phone'], message)
            
            if "error" in response:
                mongo.db.meter_readings.update_one(
                    {"_id": reading_id},
                    {"$set": {"sms_status": f"failed: {response['error']}"}}
                )
                flash(f"Reading recorded but SMS failed: {response['error']}", "warning")
            else:
                mongo.db.meter_readings.update_one(
                    {"_id": reading_id},
                    {"$set": {"sms_status": "sent"}}
                )
                flash("Reading recorded and SMS sent successfully!", "success")
                
        except Exception as sms_error:
            app.logger.error(f"SMS error: {sms_error}")
            mongo.db.meter_readings.update_one(
                {"_id": reading_id},
                {"$set": {"sms_status": f"failed: {str(sms_error)}"}}
            )
            flash("Reading recorded but SMS sending failed", "warning")
        
    except Exception as e:
        app.logger.error(f"Error recording reading: {e}")
        flash(f"Error recording reading: {str(e)}", "danger")
    
    return redirect(url_for('dashboard'))

@app.route('/record_tenant_reading/<tenant_id>', methods=['POST'])
@login_required
def record_tenant_reading(tenant_id):
    """Record water reading for a specific tenant from their details page."""
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    try:
        # Get and validate form data
        current_reading = float(request.form.get('current_reading', 0))
        previous_reading = float(request.form.get('previous_reading', 0))
        reading_date = request.form.get('reading_date')
        
        # Parse reading date
        if reading_date:
            reading_date = datetime.strptime(reading_date, '%Y-%m-%d')
        else:
            reading_date = datetime.now()
            
    except (ValueError, TypeError) as e:
        flash('Invalid reading values. Please enter valid numbers and date.', 'danger')
        return redirect(url_for('tenant_details', tenant_id=tenant_id))
    
    # Validation
    if current_reading < 0:
        flash('Readings cannot be negative', 'danger')
        return redirect(url_for('tenant_details', tenant_id=tenant_id))
    
    if current_reading <= previous_reading:
        flash('Current reading must be greater than previous reading', 'danger')
        return redirect(url_for('tenant_details', tenant_id=tenant_id))
    
    try:
        tenant_id_obj = ObjectId(tenant_id)
        tenant = mongo.db.tenants.find_one({"_id": tenant_id_obj, "admin_id": admin_id})
        
        if not tenant:
            flash('Tenant not found', 'danger')
            return redirect(url_for('dashboard'))
        
        house_number = tenant.get('house_number')
        if not house_number:
            flash('Tenant has no associated house number', 'danger')
            return redirect(url_for('tenant_details', tenant_id=tenant_id))
        
        # Get the house document to retrieve house_id
        house = mongo.db.houses.find_one({"house_number": house_number, "admin_id": admin_id})
        house_id = house.get('_id') if house else None
        
        # Calculate billing
        usage = current_reading - previous_reading
        rate_per_unit = get_rate_per_unit(admin_id)
        bill_amount = usage * rate_per_unit
        
        # Create reading records
        # Create tenant reading record (for billing purposes)
        reading_data = {
            "tenant_id": tenant_id_obj,
            "house_number": house_number,
            "house_id": house_id,
            "previous_reading": previous_reading,
            "current_reading": current_reading,
            "usage": usage,
            "bill_amount": bill_amount,
            "date_recorded": reading_date,
            "admin_id": admin_id,
            "tenant_name": tenant['name'],
            "current_tenant_id": tenant_id_obj,
            "reading_type": "tenant_specific",
            "source_collection": "meter_readings"
        }
    
        # Single insert to meter_readings
        result = mongo.db.meter_readings.insert_one(reading_data)
        reading_id = result.inserted_id
    
        # Create payment record for this bill - FIXED
        month_year = reading_date.strftime('%Y-%m')
        payment_id = create_payment_record(
            admin_id=admin_id,  # Fixed: use admin_id
            tenant_id=tenant_id_obj,  # Fixed: use tenant_id_obj
            house_id=house_id,  # Fixed: use house_id
            bill_amount=bill_amount,
            reading_id=reading_id,  # Fixed: use reading_id
            month_year=month_year,
            bill_type='water'  # Explicitly set bill_type
        )
        
        if payment_id:
            app.logger.info(f"Payment record created with ID: {payment_id}")
            cache.delete_memoized(get_billing_summary, admin_id)
            cache.delete_memoized(get_billing_summary, str(admin_id))
        else:
            app.logger.error("Failed to create payment record")
        


        admin = mongo.db.admins.find_one({"_id": admin_id})
        admin_name = admin.get('name', 'Your Landlord') if admin else 'Your Landlord'
        admin_phone = admin.get('phone', 'N/A') if admin else 'N/A'
        
        # Get payment details based on admin's payment method
        payment_info = ""
        if admin:
            if admin.get('payment_method') == 'till':
                till_number = admin.get('till', 'N/A')
                payment_info = f"Pay via Till: {till_number}"
            elif admin.get('payment_method') == 'paybill':
                business_number = admin.get('business_number', 'N/A')
                account_name = admin.get('account_name', 'N/A')
                payment_info = f"Pay via PayBill: {business_number}, Account: {account_name}"
            else:
                # Fallback for existing accounts without payment_method
                till_number = admin.get('till', 'N/A')
                payment_info = f"Pay via Till: {till_number}"
        
            # Calculate arrears excluding current month's bill
            current_month_year = get_current_month_year()
            total_arrears = calculate_total_arrears(admin_id, tenant_id_obj, bill_type='water', exclude_current_month=current_month_year)
            if total_arrears > 1:
                message = (
                    f"Water Bill Alert: {tenant['name']}, House {house_number}. "
                    f"Current bill: KES {bill_amount:.2f}. "
                    f"Outstanding arrears: KES {total_arrears:.2f}. "
                    f"Total amount due: KES {bill_amount + total_arrears:.2f}. "
                    f"{payment_info} From {admin_name} - {admin_phone}"
                )
            else:
                message = (
                    f"Water Bill Alert: {tenant['name']}, House {house_number}. "
                    f"Current bill: KES {bill_amount:.2f}. "
                    f"{payment_info} From {admin_name} - {admin_phone}"
                )
        
        # Send SMS notification
        try:
            response = send_message(tenant['phone'], message)
            
            if "error" in response:
                mongo.db.meter_readings.update_one(
                    {"_id": reading_id},
                    {"$set": {"sms_status": f"failed: {response['error']}"}}
                )
                flash(f"Reading recorded but SMS failed: {response['error']}", "warning")
            else:
                mongo.db.meter_readings.update_one(
                    {"_id": reading_id},
                    {"$set": {"sms_status": "sent"}}
                )
                flash("Reading recorded and SMS sent successfully!", "success")
                
        except Exception as sms_error:
            app.logger.error(f"SMS error: {sms_error}")
            mongo.db.meter_readings.update_one(
                {"_id": reading_id},
                {"$set": {"sms_status": f"failed: {str(sms_error)}"}}
            )
            flash("Reading recorded but SMS sending failed", "warning")
        
        flash(f'Reading recorded successfully! Usage: {usage} m³, Bill: Ksh {bill_amount}', 'success')
        app.logger.info(f"Reading recorded for tenant {tenant['name']} (ID: {tenant_id}) by admin {admin_id}")
        
    except Exception as e:
        app.logger.error(f"Error recording reading for tenant {tenant_id}: {e}")
        flash('Error recording reading. Please try again.', 'danger')
    
    return redirect(url_for('tenant_details', tenant_id=tenant_id))

@app.route('/generate_rent_bills', methods=['GET'])
@login_required
def generate_rent_bills():
    """Generate rent bills for all tenants for the current month with proper rent retrieval"""
    try:
        admin_id = get_admin_id()
        admin = mongo.db.admins.find_one({"_id": admin_id})
        admin_name = admin.get('name', 'Your Landlord') if admin else 'Your Landlord'
        admin_phone = admin.get('phone', 'N/A') if admin else 'N/A'
        
        # Get payment method details
        payment_method = admin.get('payment_method', 'till')
        if payment_method == 'till':
            payment_info = admin.get('till', 'N/A')
            payment_text = f"Pay via Till: {payment_info}"
        else:
            paybill = admin.get('business_number', 'N/A')
            account = admin.get('account_number', 'N/A')
            payment_text = f"Pay via PayBill: {paybill}, Account: {account}"
        
        # Get all active tenants for this admin
        tenants = list(mongo.db.tenants.find({"admin_id": admin_id}))
        
        if not tenants:
            flash("No active tenants found", "warning")
            return redirect(url_for('rent_dashboard'))
        
        current_month_year = get_current_month_year()
        
        # Check if bills already exist for this month
        existing_bills = list(mongo.db.payments.find({
            "admin_id": admin_id,
            "month_year": current_month_year,
            "bill_type": "rent"
        }))
        
        if existing_bills:
            flash(f"Rent bills for {current_month_year} have already been generated", "warning")
            return redirect(url_for('rent_dashboard'))
        
        bills_generated = 0
        sms_sent = 0
        
        for tenant in tenants:
            tenant_id = tenant["_id"]
            house_number = tenant.get("house_number")
            
            if not house_number:
                app.logger.warning(f"Tenant {tenant.get('name')} has no house number")
                continue
            
            # Find house document with explicit rent field projection
            house = mongo.db.houses.find_one(
                {"house_number": house_number, "admin_id": admin_id},
                {"_id": 1, "rent": 1, "house_number": 1}  # Explicit projection
            )
            
            if not house:
                app.logger.warning(f"House {house_number} not found for tenant {tenant.get('name')}")
                continue
            
            house_id = house["_id"]
            
            # Get rent amount with multiple fallback options
            rent_amount = None
            
            # Priority 1: Rent from houses collection
            if house.get("rent") and house["rent"] > 0:
                rent_amount = float(house["rent"])
            # Priority 2: Rent from tenant record
            elif tenant.get("rent_amount") and tenant["rent_amount"] > 0:
                rent_amount = float(tenant["rent_amount"])
            # Priority 3: Default rent from admin settings
            elif admin.get("default_rent") and admin["default_rent"] > 0:
                rent_amount = float(admin["default_rent"])
            
            if not rent_amount or rent_amount <= 0:
                app.logger.warning(f"No valid rent amount found for tenant {tenant.get('name')}, house {house_number}")
                continue
            
            # Create payment record for rent
            payment_id = create_payment_record(
                admin_id=admin_id,
                tenant_id=tenant_id,
                house_id=house_id,
                bill_amount=rent_amount,
                month_year=current_month_year,
                bill_type="rent"
            )
            
            if payment_id:
                bills_generated += 1

                tenant_id_obj = ObjectId(tenant_id)
                tenant = mongo.db.tenants.find_one({"_id": tenant_id})
                # Calculate arrears (excluding current bill)
                total_arrears = calculate_total_arrears(admin_id, tenant_id_obj, bill_type='rent', exclude_current_month=current_month_year)
                # Create SMS message with arrears info
                if total_arrears > 1:  # Use smaller threshold for precision
                    message = (
                        f"Rent Bill Alert: {tenant['name']}, House {house_number}. "
                        f"Current bill: KES {rent_amount:.2f}. "
                        f"Outstanding arrears: KES {total_arrears:.2f}. "
                        f"Total amount due: KES {rent_amount + total_arrears:.2f}. "
                        f"{payment_text} From {admin_name} - {admin_phone}"
                    )
                else:
                    message = (
                        f"Rent Bill Alert: {tenant['name']}, House {house_number}. "
                        f"Current bill: KES {rent_amount:.2f}. "
                        f"{payment_text} From {admin_name} - {admin_phone}"
                    )
                
                # Send SMS
                try:
                    if tenant.get('phone'):
                        response = send_message(tenant['phone'], message)
                        
                        if "error" not in response:
                            sms_sent += 1
                        else:
                            app.logger.error(f"SMS error for tenant {tenant['name']}: {response.get('error')}")
                    else:
                        app.logger.warning(f"No phone number for tenant {tenant['name']}")
                        
                except Exception as sms_error:
                    app.logger.error(f"SMS error for tenant {tenant['name']}: {sms_error}")
        
        # Invalidate billing summary cache
        cache.delete_memoized(get_billing_summary, admin_id)
        cache.delete_memoized(get_billing_summary, str(admin_id))
        
        if bills_generated > 0:
            flash(f"Generated {bills_generated} rent bills. {sms_sent} SMS notifications sent.", "success")
        else:
            flash("No rent bills were generated. Please check tenant and house configurations.", "warning")
        
    except Exception as e:
        app.logger.error(f"Error generating rent bills: {str(e)}")
        flash(f"Error generating rent bills: {str(e)}", "danger")
    
    return redirect(url_for('rent_dashboard'))

@app.route('/rent_dashboard', methods=['GET', 'POST'])
@login_required
def rent_dashboard():
    """Display rent dashboard with all rent bills"""
    try:
        # Get admin_id from session with ObjectId conversion
        admin_id = get_admin_id()

        # Get pagination parameters from request
        page = request.args.get('page', 1, type=int)
        filter_status = request.args.get('filter', 'unpaid_partial')
        search_term = request.args.get('search', '')
        
        # Get billing summary using aggregation pipeline with bill_type filter
        billing_summary = get_billing_summary(admin_id, bill_type='rent')
        pagination = get_unpaid_bills_paginated(
            admin_id, page=page, per_page=10, 
            filter_status=filter_status, search_term=search_term,
            bill_type='rent'
        )
        
        # Enrich with tenant and house information if not already done in aggregation
        enriched_bills = []
        for bill in pagination['bills']:
            # Check if tenant_name and house_number are already in the bill (from aggregation)
            if 'tenant_name' not in bill or 'house_number' not in bill:
                tenant = mongo.db.tenants.find_one({'_id': bill['tenant_id']})
                house = mongo.db.houses.find_one({'_id': bill['house_id']})
                
                bill['tenant_name'] = tenant['name'] if tenant else 'Unknown'
                bill['house_number'] = house['house_number'] if house else 'Unknown'
            
            bill['outstanding_amount'] = bill['bill_amount'] - bill.get('amount_paid', 0)
            enriched_bills.append(bill)
        
        return render_template('rent_dashboard.html', 
                               bills=enriched_bills, 
                               total_ever_billed=billing_summary['total_ever_billed'],
                               total_ever_collected=billing_summary['total_ever_collected'],
                               pagination=pagination,
                               now=datetime.now().timestamp(),
                               now_date=datetime.now())
        
    except KeyError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    except Exception as e:
        app.logger.error(f"Error in rent dashboard: {str(e)}")
        flash(f'Error loading rent dashboard: {str(e)}', 'danger')
        return redirect(url_for('dashboard'))

@cache.memoize(timeout=3)  # Cache for 1 hour
def get_billing_summary(admin_id, bill_type=None):
    """Get billing summary using MongoDB aggregation pipeline"""
    try:
        # Ensure admin_id is an ObjectId
        if isinstance(admin_id, str):
            admin_id = ObjectId(admin_id)
        
        # Start with basic match criteria
        match_criteria = {'admin_id': admin_id}
        
        # Add bill_type filter if specified
        if bill_type:
            match_criteria['bill_type'] = bill_type
            
        pipeline = [
            {'$match': match_criteria},
            {'$group': {
                '_id': None,
                'total_ever_billed': {'$sum': '$bill_amount'},
                'total_ever_collected': {'$sum': {'$ifNull': ['$amount_paid', 0]}},
                'count': {'$sum': 1}
            }}
        ]
        
        result = list(mongo.db.payments.aggregate(pipeline))
        if result:
            return result[0]
        return {'total_ever_billed': 0, 'total_ever_collected': 0, 'count': 0}
    except Exception as e:
        app.logger.error(f"Error in billing summary aggregation: {str(e)}")
        return {'total_ever_billed': 0, 'total_ever_collected': 0, 'count': 0}

@app.route('/payments_dashboard', methods=['GET', 'POST'])
@login_required
def payments_dashboard():
    """Display payments dashboard with all unpaid bills"""
    try:
        # Get admin_id from session with ObjectId conversion
        admin_id = get_admin_id()

        # Get pagination parameters from request
        page = request.args.get('page', 1, type=int)
        filter_status = request.args.get('filter', 'unpaid_partial')
        search_term = request.args.get('search', '')
        
        # Get billing summary using aggregation pipeline
        billing_summary = get_billing_summary(admin_id, bill_type='water')
        pagination = get_unpaid_bills_paginated(
            admin_id, page=page, per_page=10, 
            filter_status=filter_status, search_term=search_term,
            bill_type='water'

        )
        
        # Enrich with tenant and house information if not already done in aggregation
        enriched_bills = []
        for bill in pagination['bills']:
            # Check if tenant_name and house_number are already in the bill (from aggregation)
            if 'tenant_name' not in bill or 'house_number' not in bill:
                tenant = mongo.db.tenants.find_one({'_id': bill['tenant_id']})
                house = mongo.db.houses.find_one({'_id': bill['house_id']})
                
                bill['tenant_name'] = tenant['name'] if tenant else 'Unknown'
                bill['house_number'] = house['house_number'] if house else 'Unknown'
            
            bill['outstanding_amount'] = bill['bill_amount'] - bill.get('amount_paid', 0)
            enriched_bills.append(bill)
        
        return render_template('payments_dashboard.html', 
                               bills=enriched_bills, 
                               total_ever_billed=billing_summary['total_ever_billed'],
                               total_ever_collected=billing_summary['total_ever_collected'],
                               pagination=pagination,
                               now=datetime.now().timestamp())
                            

        
    except KeyError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    except Exception as e:
        app.logger.error(f"Error in payments dashboard: {str(e)}")
        flash(f'Error loading payments dashboard: {str(e)}', 'danger')
        return redirect(url_for('dashboard'))

@app.route('/record_payment/<payment_id>', methods=['POST'])
@login_required
def record_payment(payment_id):
    """Record a payment for a specific bill with proper status updates"""
    try:
        amount_paid = float(request.form.get('amount_paid', 0))
        payment_method = request.form.get('payment_method', 'cash')
        notes = request.form.get('notes', '')
        
        if amount_paid <= 0:
            flash('Payment amount must be greater than 0', 'danger')
            return redirect(request.referrer or url_for('payments_dashboard'))
        
        # Get the current payment record
        payment = mongo.db.payments.find_one({"_id": ObjectId(payment_id)})
        
        if not payment:
            flash('Payment record not found', 'danger')
            return redirect(request.referrer or url_for('payments_dashboard'))
        
        # Verify admin ownership
        admin_id = ObjectId(session.get('admin_id'))
        if payment.get('admin_id') != admin_id:
            flash('Unauthorized access to payment record', 'danger')
            return redirect(request.referrer or url_for('payments_dashboard'))
        
        # Calculate new payment amounts
        current_amount_paid = payment.get('amount_paid', 0)
        bill_amount = payment['bill_amount']
        new_total_paid = current_amount_paid + amount_paid
        
        # Prevent overpayment
        if new_total_paid > bill_amount:
            overpayment = new_total_paid - bill_amount
            flash(f'Payment amount exceeds bill amount by KES {overpayment:.2f}. Please enter a smaller amount.', 'warning')
            return redirect(request.referrer or url_for('payments_dashboard'))
        
        # Determine new payment status
        if new_total_paid >= bill_amount:
            payment_status = 'paid'
            new_total_paid = bill_amount  # Ensure exact match
        elif new_total_paid > 0:
            payment_status = 'partial'
        else:
            payment_status = 'unpaid'
        
        # Update payment record with comprehensive tracking
        update_result = mongo.db.payments.update_one(
            {"_id": ObjectId(payment_id)},
            {
                "$set": {
                    "amount_paid": round(new_total_paid, 2),
                    "payment_status": payment_status,
                    "last_payment_date": datetime.now(),
                    "last_payment_method": payment_method,
                    "updated_at": datetime.now()
                },
                "$push": {
                    "payment_history": {
                        "amount": round(amount_paid, 2),
                        "method": payment_method,
                        "date": datetime.now(),
                        "notes": notes,
                        "recorded_by": admin_id
                    }
                },
                "$inc": {
                    "payment_count": 1
                }
            }
        )
        
        if update_result.modified_count > 0:
            # Invalidate cached billing summary
            cache.delete_memoized(get_billing_summary, admin_id)
            cache.delete_memoized(get_billing_summary, str(admin_id))
            cache.delete_memoized(get_billing_summary, admin_id, 'water')
            cache.delete_memoized(get_billing_summary, str(admin_id), 'water')

            # Send SMS notification if bill is fully paid
            # Create success message based on payment status
            if payment_status == 'paid':
                flash(f'Payment of KES {amount_paid:.2f} recorded successfully. Bill is now fully paid.', 'success')
            else:
                outstanding = bill_amount - new_total_paid
                flash(f'Partial payment of KES {amount_paid:.2f} recorded. Outstanding balance: KES {outstanding:.2f}', 'info')
        else:
            flash('Error updating payment record', 'danger')
            
    except ValueError:
        flash('Invalid payment amount. Please enter a valid number.', 'danger')
    except Exception as e:
        app.logger.error(f"Error recording payment: {str(e)}")
        flash(f'Error processing payment: {str(e)}', 'danger')
    
    # Redirect back to the referring page
    return redirect(request.referrer or url_for('payments_dashboard'))


@app.route('/export_tenant_data/<tenant_id>')
@login_required
def export_tenant_data(tenant_id):
    """Export individual tenant's reading history to Excel."""
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    try:
        tenant_id_obj = ObjectId(tenant_id)
        tenant = mongo.db.tenants.find_one({"_id": tenant_id_obj, "admin_id": admin_id})
        
        if not tenant:
            flash('Tenant not found', 'danger')
            return redirect(url_for('dashboard'))
        
        # Get tenant's reading history
        readings = list(mongo.db.meter_readings.find({
            "tenant_id": tenant_id_obj,
            "admin_id": admin_id
        }).sort("date_recorded", 1))
        
        # Create Excel file in memory
        output = BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        
        # Create worksheet
        worksheet = workbook.add_worksheet(f"{tenant['name']}_History")
        
        # Define formats
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#4472C4',
            'font_color': 'white',
            'border': 1,
            'align': 'center'
        })
        
        cell_format = workbook.add_format({
            'border': 1,
            'align': 'center'
        })
        
        currency_format = workbook.add_format({
            'border': 1,
            'align': 'center',
            'num_format': '"Ksh "#,##0.00'
        })
        
        date_format = workbook.add_format({
            'border': 1,
            'align': 'center',
            'num_format': 'yyyy-mm-dd'
        })
        
        # Write tenant information
        worksheet.write('A1', 'Tenant Information', header_format)
        worksheet.write('A2', 'Name:', cell_format)
        worksheet.write('B2', tenant['name'], cell_format)
        worksheet.write('A3', 'Phone:', cell_format)
        worksheet.write('B3', tenant['phone'], cell_format)
        worksheet.write('A4', 'House Number:', cell_format)
        worksheet.write('B4', tenant['house_number'], cell_format)
        
        # Write reading history headers
        headers = ['Date', 'Previous Reading (m³)', 'Current Reading (m³)', 'Usage (m³)', 'Bill Amount']
        start_row = 6
        
        worksheet.write('A6', 'Reading History', header_format)
        for col, header in enumerate(headers):
            worksheet.write(start_row + 1, col, header, header_format)
        
        # Write reading data
        for row, reading in enumerate(readings, start_row + 2):
            worksheet.write(row, 0, reading['date_recorded'], date_format)
            worksheet.write(row, 1, reading['previous_reading'], cell_format)
            worksheet.write(row, 2, reading['current_reading'], cell_format)
            worksheet.write(row, 3, reading['usage'], cell_format)
            worksheet.write(row, 4, reading['bill_amount'], currency_format)
        
        # Add summary statistics
        if readings:
            summary_row = start_row + len(readings) + 3
            worksheet.write(summary_row, 0, 'Summary Statistics', header_format)
            worksheet.write(summary_row + 1, 0, 'Total Readings:', cell_format)
            worksheet.write(summary_row + 1, 1, len(readings), cell_format)
            worksheet.write(summary_row + 2, 0, 'Total Usage:', cell_format)
            worksheet.write(summary_row + 2, 1, sum(r['usage'] for r in readings), cell_format)
            worksheet.write(summary_row + 3, 0, 'Total Amount:', cell_format)
            worksheet.write(summary_row + 3, 1, sum(r['bill_amount'] for r in readings), currency_format)
            worksheet.write(summary_row + 4, 0, 'Average Usage:', cell_format)
            worksheet.write(summary_row + 4, 1, sum(r['usage'] for r in readings) / len(readings), cell_format)
        
        # Auto-adjust column widths
        worksheet.set_column('A:A', 15)
        worksheet.set_column('B:E', 20)
        
        workbook.close()
        output.seek(0)
        
        # Generate filename
        filename = f"{tenant['name']}_reading_history_{datetime.now().strftime('%Y%m%d')}.xlsx"
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        app.logger.error(f"Error exporting tenant data for {tenant_id}: {e}")
        flash('Error generating export file. Please try again.', 'danger')
        return redirect(url_for('tenant_details', tenant_id=tenant_id))
        
@app.route('/sms_config', methods=['GET', 'POST'])
@login_required
def sms_config():
    # Get admin_id from session
    admin_id = get_admin_id()
    
    # Get admin information
    admin = mongo.db.admins.find_one({"_id": admin_id})
    
    # Add admin_id to query
    config = mongo.db.sms_config.find_one({"admin_id": admin_id})

    if request.method == 'POST':
        rate_per_unit = request.form.get('rate_per_unit', type=float)

        if rate_per_unit is None:
            flash('Rate per unit is required', 'danger')
            return redirect(url_for('sms_config'))

        try:
            if config:
                mongo.db.sms_config.update_one(
                    {"_id": config["_id"]},
                    {"$set": {
                        "rate_per_unit": rate_per_unit,
                        "updated_at": datetime.now()
                    }}
                )
            else:
                mongo.db.sms_config.insert_one({
                    "rate_per_unit": rate_per_unit,
                    "updated_at": datetime.now(),
                    "admin_id": admin_id  # Add admin_id
                })
            flash('SMS configuration updated successfully!', 'success')
        except Exception as e:
            flash(f'Error updating SMS configuration: {str(e)}', 'danger')

    return render_template('sms_config.html', 
                          config=config,
                          admin=admin,  # Pass admin info to template
                          rate_per_unit=RATE_PER_UNIT,
                          talksasa_api_key=TALKSASA_API_KEY,
                          talksasa_sender_id=TALKSASA_SENDER_ID)

# Add these routes after the sms_config route

@app.route('/update_profile', methods=['POST'])
@login_required
def update_profile():
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    # Get admin to check payment method
    admin = mongo.db.admins.find_one({"_id": admin_id})
    if not admin:
        flash('Admin not found', 'danger')
        return redirect(url_for('login'))
    
    # Get form data
    name = request.form.get('name', '').strip()
    phone = request.form.get('phone', '').strip()

    update_data = {
        "name": name,
        "username": name, 
        # Also update username to match name
        "phone": phone,
        "updated_at": datetime.now()
    }
    
    # Handle payment method specific fields
    if admin.get('payment_method') == 'paybill':
        business_number = request.form.get('business_number', '').strip()
        account_name = request.form.get('account_name', '').strip()
        
        if not all([name, business_number, account_name]):
            flash('All fields are required', 'danger')
            return redirect(url_for('sms_config'))
        
        # Check if paybill already exists with another admin
        existing_admin = mongo.db.admins.find_one({
            "business_number": business_number, 
            "account_name": account_name,
            "_id": {"$ne": admin_id}
        })
        if existing_admin:
            flash('An account with this PayBill configuration already exists', 'danger')
            return redirect(url_for('sms_config'))
        
        update_data.update({
            "business_number": business_number,
            "account_name": account_name
        })
    else:
        # Default to till method
        till = request.form.get('till', '').strip()
        
        if not all([name, till]):
            flash('All fields are required', 'danger')
            return redirect(url_for('sms_config'))
        
        # Check if till already exists with another admin
        existing_admin = mongo.db.admins.find_one({"till": till, "_id": {"$ne": admin_id}})
        if existing_admin:
            flash('An account with this till number already exists', 'danger')
            return redirect(url_for('sms_config'))
        
        update_data["till"] = till
    
    try:
        # Update admin profile
        mongo.db.admins.update_one(
            {"_id": admin_id},
            {"$set": update_data}
        )
        flash('Profile updated successfully!', 'success')
    except Exception as e:
        app.logger.error(f"Error updating profile: {e}")
        flash(f'Error updating profile: {str(e)}', 'danger')
    
    return redirect(url_for('sms_config'))


@app.route('/change_password', methods=['POST'])
@login_required
def change_password():
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    # Get form data
    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')
    
    # Validate inputs
    if not all([current_password, new_password, confirm_password]):
        flash('All fields are required', 'danger')
        return redirect(url_for('sms_config'))
    
    if new_password != confirm_password:
        flash('New passwords do not match', 'danger')
        return redirect(url_for('sms_config'))
    
    # Verify current password
    admin = mongo.db.admins.find_one({"_id": admin_id})
    if not admin or not check_password_hash(admin['password'], current_password):
        flash('Current password is incorrect', 'danger')
        return redirect(url_for('sms_config'))
    
    try:
        # Update password
        mongo.db.admins.update_one(
            {"_id": admin_id},
            {"$set": {
                "password": generate_password_hash(new_password),
                "updated_at": datetime.now()
            }}
        )
        flash('Password changed successfully!', 'success')
    except Exception as e:
        app.logger.error(f"Error changing password: {e}")
        flash(f'Error changing password: {str(e)}', 'danger')
    
    return redirect(url_for('sms_config'))


@app.route('/edit_tenant/<tenant_id>', methods=['POST'])
@login_required
def edit_tenant(tenant_id):
    """Edit tenant with improved house relationship management."""
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    # Get form data
    name = request.form.get('name', '').strip()
    house_number = request.form.get('house_number', '').strip()
    phone = request.form.get('phone', '').strip()
    
    # Validate inputs
    if not all([name, house_number, phone]):
        flash('All fields are required', 'danger')
        return redirect(url_for('tenant_details', tenant_id=tenant_id))
    
    try:
        tenant_id_obj = ObjectId(tenant_id)
        tenant = mongo.db.tenants.find_one({"_id": tenant_id_obj, "admin_id": admin_id})
        
        if not tenant:
            flash('Tenant not found', 'danger')
            return redirect(url_for('dashboard'))
        
        # Get old house number for comparison
        old_house_number = tenant.get('house_number', '')
        
        # Format phone number
        formatted_phone = format_phone_number(phone)
        
        # Check if phone number already exists for another tenant
        if formatted_phone != tenant.get('phone', ''):
            existing_tenant = mongo.db.tenants.find_one({
                "phone": formatted_phone,
                "admin_id": admin_id,
                "_id": {"$ne": tenant_id_obj}
            })
            
            if existing_tenant:
                flash(f'Phone number already exists for tenant: {existing_tenant["name"]}', 'danger')
                return redirect(url_for('tenant_details', tenant_id=tenant_id))
        unpaid_bills = get_unpaid_bills(admin_id, tenant_id_obj)

        if unpaid_bills:
            total_arrears = calculate_total_arrears(admin_id, tenant_id_obj)
            flash(f'Cannot edit tenant with unpaid bills. Outstanding amount: KSh{total_arrears:.2f}. Please settle all bills before making changes.', 'danger')
            return redirect(url_for('dashboard'))
        else:
            # If house number is changing, check availability
            if house_number != old_house_number:
                # Check if new house exists and is available
                new_house = mongo.db.houses.find_one({
                    "house_number": house_number,
                    "admin_id": admin_id
                })
                
                if new_house and new_house.get("is_occupied"):
                    # Get tenant name from house document
                    tenant_name = new_house.get("current_tenant_name", "Unknown")
                    
                    # If tenant name not in house document, try to find it from tenants collection
                    if tenant_name == "Unknown" and "current_tenant_id" in new_house:
                        tenant_doc = mongo.db.tenants.find_one({"_id": new_house["current_tenant_id"]})
                        if tenant_doc:
                            tenant_name = tenant_doc.get("name", "Unknown")
                            
                    flash(f'House {house_number} is already occupied by {tenant_name}', 'danger')
                    return redirect(url_for('tenant_details', tenant_id=tenant_id))
                
                # Update old house to be unoccupied
                if old_house_number:
                    old_house = mongo.db.houses.find_one({
                        "house_number": old_house_number,
                        "admin_id": admin_id
                    })
                    
                    if old_house:
                        mongo.db.houses.update_one(
                            {"_id": old_house["_id"]},
                            {"$set": {
                                "is_occupied": False,
                                "current_tenant_id": None,
                                "current_tenant_name": None
                            }}
                        )
                
                # Create or update new house
                if new_house:
                    # Update existing house
                    mongo.db.houses.update_one(
                        {"_id": new_house["_id"]},
                        {"$set": {
                            "is_occupied": True,
                            "current_tenant_id": tenant_id_obj,
                            "current_tenant_name": name
                        }}
                    )
                    
                    # Store house_id in tenant document
                    house_id = new_house["_id"]
                else:
                    # Create new house
                    house_id = ObjectId()
                    new_house_doc = {
                        "_id": house_id,
                        "house_number": house_number,
                        "is_occupied": True,
                        "current_tenant_id": tenant_id_obj,
                        "current_tenant_name": name,
                        "admin_id": admin_id,
                        "created_at": datetime.now(),
                        "rent": 0
                    }
                    mongo.db.houses.insert_one(new_house_doc)
                
                # Update tenant with new house info
                mongo.db.tenants.update_one(
                    {"_id": tenant_id_obj},
                    {"$set": {
                        "name": name,
                        "phone": formatted_phone,
                        "house_number": house_number,
                        "house_id": house_id
                    }}
                )
            else:
                # House is not changing, just update tenant info
                mongo.db.tenants.update_one(
                    {"_id": tenant_id_obj},
                    {"$set": {
                        "name": name,
                        "phone": formatted_phone
                    }}
                )
                
                # Update house with current tenant name (in case name changed)
                house = mongo.db.houses.find_one({
                    "house_number": house_number,
                    "admin_id": admin_id
                })
                
                if house:
                    mongo.db.houses.update_one(
                        {"_id": house["_id"]},
                        {"$set": {"current_tenant_name": name}}
                    )
            
        flash('Tenant updated successfully', 'success')
        
    except ValueError as e:
        flash(f'Error: {str(e)}', 'danger')
    except Exception as e:
        app.logger.error(f"Error updating tenant: {e}")
        flash('An error occurred while updating the tenant', 'danger')
    
    return redirect(url_for('tenant_details', tenant_id=tenant_id))


@app.route('/delete_tenant/<tenant_id>', methods=['POST'])
@login_required
def delete_tenant(tenant_id):
    # Get admin_id from session
    admin_id = get_admin_id()
    tenant_id_obj = ObjectId(tenant_id)
    unpaid_bills = get_unpaid_bills(admin_id, tenant_id_obj)
    if unpaid_bills:
        total_arrears = calculate_total_arrears(admin_id, tenant_id_obj)
        flash(f'Cannot delete tenant with unpaid bills. Outstanding amount: KSh{total_arrears:.2f}. Please settle all bills before deletion.', 'danger')
        return redirect(url_for('dashboard'))

    tenant_id_obj = ObjectId(tenant_id)
    # Add admin_id to query
    tenant = mongo.db.tenants.find_one({"_id": tenant_id_obj, "admin_id": admin_id})
    
    if not tenant:
        flash('Tenant not found', 'danger')
        return redirect(url_for('dashboard'))
    
    # Get house number before deleting tenant
    house_number = tenant.get('house_number')
    
    # Delete tenant
    mongo.db.tenants.delete_one({"_id": tenant_id_obj, "admin_id": admin_id})
    
    # Update house status if house exists
    if house_number:
        mongo.db.houses.update_one(
            {"house_number": house_number, "admin_id": admin_id},
            {"$set": {
                "is_occupied": False,
                "current_tenant_id": None,
                "current_tenant_name": None
            }}
        )
    
    flash('Tenant deleted successfully', 'success')
    return redirect(url_for('dashboard'))

@app.route('/test_sms', methods=['POST'])
@login_required
def test_sms():
    # Get admin_id from session
    admin_id = get_admin_id()
    
    phone = request.form.get('test_phone', '').strip()
    message = request.form.get('test_message', '').strip()
    
    if not phone or not message:
        flash('Phone number and message are required', 'danger')
        return redirect(url_for('sms_config'))
    
    try:
        # Format phone number
        formatted_phone = format_phone_number(phone)
        
        # Send test message
        response = send_message(formatted_phone, message)
        
        if "error" in response:
            flash(f"Failed to send test SMS: {response['error']}", 'danger')
        else:
            # Log the test message with admin_id
            mongo.db.sms_logs.insert_one({
                "phone": formatted_phone,
                "message": message,
                "status": "sent",
                "admin_id": admin_id,
                "sent_at": datetime.now()
            })
            flash('Test SMS sent successfully!', 'success')
            
    except ValueError as e:
        flash(str(e), 'danger')
    except Exception as e:
        flash(f"Error sending test SMS: {str(e)}", 'danger')
    
    return redirect(url_for('sms_config'))

@app.route("/robots.txt")
def robots_txt():
    return send_from_directory("static", "robots.txt")

@app.route("/sitemap.xml")
def sitemap_xml():
    return send_from_directory("static", "sitemap.xml")

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500

@app.route('/db_error')
def db_error():
    return render_template('error.html', 
                          error_message="Database connection is not available. Please try again later.")

def create_admin_user():
    if mongo.db.admins.count_documents({}) == 0:
        default_username = os.environ.get('DEFAULT_ADMIN_USERNAME')
        default_password = os.environ.get('DEFAULT_ADMIN_PASSWORD')
        if not default_username or not default_password:
            app.logger.error("Default admin credentials not provided in environment variables.")
            return
        try:
            admin = {
                "username": default_username,
                "password": generate_password_hash(default_password),
                "created_at": datetime.now()
            }
            mongo.db.admins.insert_one(admin)
            app.logger.info(
                f"Created default admin user: {default_username}. Please change the default credentials immediately.")
            print(f"Created default admin user: {default_username}. Please change the default credentials immediately.")
        except Exception as e:
            app.logger.error(f"Failed to create default admin: {e}")


@app.route('/import_tenants', methods=['POST'])
@login_required
def import_tenants():
    """Import tenants from Excel with improved house relationship management."""
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    admin = mongo.db.admins.find_one({"_id": admin_id})
    tier = admin.get('subscription_tier', 'free')
    tier_config = SUBSCRIPTION_TIERS.get(tier, SUBSCRIPTION_TIERS['starter'])
    max_tenants = tier_config['max_tenants']
    current_tenants = mongo.db.tenants.count_documents({"admin_id": admin_id})

    if 'excel_file' not in request.files:
        flash('No file uploaded', 'danger')
        return redirect(url_for('dashboard'))
    
    file = request.files['excel_file']
    if not file or file.filename == '':
        flash('No file selected', 'danger')
        return redirect(url_for('dashboard'))
    
    if not file.filename.lower().endswith(('.xlsx', '.xls')):
        flash('Invalid file format. Please upload Excel files only (.xlsx or .xls)', 'danger')
        return redirect(url_for('dashboard'))
    
    try:
        # Read Excel file with better error handling
        df = pd.read_excel(file)
        new_tenants_count = len(df)

        if max_tenants != -1 and (current_tenants + new_tenants_count) > max_tenants:
            allowed = max(0, max_tenants - current_tenants)
            flash(
                f'Import would exceed your tenant limit. You can add {allowed} more tenants '
                f'with your {tier_config["name"]} plan. Please upgrade or reduce the import size.',
                'warning'
            )
            return redirect(url_for('subscription'))
        else:
            # Validate required columns
            required_columns = ['name', 'house_number', 'phone']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            if missing_columns:
                flash(f"Missing required columns: {', '.join(missing_columns)}", 'danger')
                return redirect(url_for('dashboard'))
            
            # Pre-fetch existing data for validation - only for current admin
            existing_phones = set(
                tenant['phone'] for tenant in 
                mongo.db.tenants.find({"admin_id": admin_id}, {"phone": 1})
                if 'phone' in tenant
            )
            
            # Get occupied houses for this admin only
            occupied_houses = {}
            for house in mongo.db.houses.find({"admin_id": admin_id, "is_occupied": True}):
                if 'house_number' in house:
                    occupied_houses[house['house_number']] = {
                        'id': house['_id'],
                        'tenant_id': house.get('current_tenant_id'),
                        'tenant_name': house.get('current_tenant_name', 'Unknown')
                    }
            
            # Process rows with batch operations
            success_count = 0
            error_count = 0
            error_messages = []
            
            tenants_to_insert = []
            houses_to_create = []
            readings_to_insert = []
            
            # Track phone numbers and house numbers within the import file to check for duplicates
            import_phones = set()
            import_houses = set()
            
            for index, row in df.iterrows():
                try:
                    # Clean and validate data
                    name = str(row['name']).strip()
                    house_number = str(row['house_number']).strip()
                    phone = str(row['phone']).strip()
                    last_reading = pd.to_numeric(row.get('last_reading', 0), errors='coerce') or 0
                    
                    if not all([name, house_number, phone]):
                        error_messages.append(f"Row {index+2}: Missing required data")
                        error_count += 1
                        continue
                    
                    # Enhanced phone validation
                    try:
                        # Format the phone number
                        formatted_phone = format_phone_number(phone)
                        
                        # Ensure format_phone_number returned a value
                        if not formatted_phone:
                            raise ValueError("Phone number formatting failed")
                            
                    except ValueError as ve:
                        error_messages.append(f"Row {index+2}: {str(ve)}")
                        error_count += 1
                        continue
                    
                    # Check for duplicates in existing database (for this admin only)
                    if formatted_phone in existing_phones:
                        # Find the tenant with this phone number to show more helpful error
                        existing_tenant = mongo.db.tenants.find_one({"phone": formatted_phone, "admin_id": admin_id})
                        tenant_name = existing_tenant.get('name', 'Unknown') if existing_tenant else 'Unknown'
                        tenant_house = existing_tenant.get('house_number', 'Unknown') if existing_tenant else 'Unknown'
                        error_messages.append(f"Row {index+2}: Phone number {formatted_phone} already exists for tenant {tenant_name} in house {tenant_house}")
                        error_count += 1
                        continue
                    
                    # Check for duplicate phone numbers within the import file
                    if formatted_phone in import_phones:
                        error_messages.append(f"Row {index+2}: Duplicate phone number {formatted_phone} in import file")
                        error_count += 1
                        continue
                    
                    # Check for duplicate house numbers in existing database (for this admin only)
                    if house_number in occupied_houses:
                        house_info = occupied_houses[house_number]
                        tenant_name = house_info['tenant_name']
                        error_messages.append(f"Row {index+2}: House {house_number} is already occupied by {tenant_name}")
                        error_count += 1
                        continue
                    
                    # Check for duplicate house numbers within the import file
                    if house_number in import_houses:
                        error_messages.append(f"Row {index+2}: Duplicate house number {house_number} in import file")
                        error_count += 1
                        continue
                    
                    # Add to tracking sets for this import
                    import_phones.add(formatted_phone)
                    import_houses.add(house_number)
                    
                    # Check if house exists but is unoccupied
                    existing_house = mongo.db.houses.find_one({
                        "house_number": house_number,
                        "admin_id": admin_id,
                        "is_occupied": False
                    })
                    
                    # Prepare tenant data
                    tenant_id = ObjectId()
                    new_tenant = {
                        "_id": tenant_id,
                        "name": name,
                        "phone": formatted_phone,
                        "house_number": house_number,
                        "admin_id": admin_id,
                        "created_at": datetime.now()
                    }
                    
                    # Prepare house data
                    if existing_house:
                        # Use existing house ID
                        house_id = existing_house["_id"]
                        new_tenant["house_id"] = house_id
                        
                        # Update existing house
                        mongo.db.houses.update_one(
                            {"_id": house_id},
                            {"$set": {
                                "is_occupied": True,
                                "current_tenant_id": tenant_id,
                                "current_tenant_name": name
                            }}
                        )
                    else:
                        # Create new house with new ID
                        house_id = ObjectId()
                        new_tenant["house_id"] = house_id
                        
                        houses_to_create.append({
                            "_id": house_id,
                            "house_number": house_number,
                            "is_occupied": True,
                            "current_tenant_id": tenant_id,
                            "current_tenant_name": name,
                            "admin_id": admin_id,
                            "created_at": datetime.now()
                        })
                    
                    tenants_to_insert.append(new_tenant)
                    existing_phones.add(formatted_phone)
                    occupied_houses[house_number] = {
                        'id': house_id,
                        'tenant_id': tenant_id,
                        'tenant_name': name
                    }
                    
                    # Prepare initial reading if provided
                    if last_reading > 0:
                        rate = get_rate_per_unit(admin_id)
                        bill_amount = last_reading * rate
                        reading_id = ObjectId()
                        readings_to_insert.append({
                            "_id": reading_id,
                            "tenant_id": tenant_id,
                            "house_number": house_number,
                            "house_id": house_id,  # Add house_id to readings
                            "previous_reading": 0,
                            "current_reading": last_reading,
                            "usage": last_reading,
                            "bill_amount": bill_amount,
                            "admin_id": admin_id,
                            "date_recorded": datetime.now(),
                            "sms_status": "not_sent"
                        })
                        
                        # Create payment record for this reading
                        month_year = datetime.now().strftime('%Y-%m')
                        payment_data = {
                            'admin_id': admin_id,
                            'tenant_id': tenant_id,
                            'house_id': house_id,
                            'bill_amount': bill_amount,
                            'amount_paid': 0.0,
                            'payment_status': 'unpaid',
                            'due_date': datetime.now() + timedelta(days=30),
                            'month_year': month_year,
                            'reading_id': reading_id,
                            'last_payment_date': None,
                            'last_payment_method': None,
                            'notes': '',
                            'created_at': datetime.now(),
                            'updated_at': datetime.now()
                        }
                        
                        # Add to batch insert for payments
                        if 'payments_to_insert' not in locals():
                            payments_to_insert = []
                        payments_to_insert.append(payment_data)
                    
                    success_count += 1
                    
                except Exception as e:
                    app.logger.error(f"Error processing row {index+2}: {e}")
                    error_messages.append(f"Row {index+2}: {str(e)}")
                    error_count += 1
            
            # Batch insert operations
            if tenants_to_insert:
                mongo.db.tenants.insert_many(tenants_to_insert)
                
            if houses_to_create:
                # Insert new houses
                mongo.db.houses.insert_many(houses_to_create)
                
            if readings_to_insert:
                mongo.db.meter_readings.insert_many(readings_to_insert)
                
            # Insert payment records if any
            if 'payments_to_insert' in locals() and payments_to_insert:
                mongo.db.payments.insert_many(payments_to_insert)
            
            # Show results
            if success_count > 0:
                flash(f'Successfully imported {success_count} tenants', 'success')
            
            if error_count > 0:
                flash(f'Failed to import {error_count} tenants', 'warning')
                # Show first 5 errors to avoid overwhelming the user
                for msg in error_messages[:5]:
                    flash(msg, 'warning')
                if len(error_messages) > 5:
                    flash(f'... and {len(error_messages) - 5} more errors', 'warning')
        
    except Exception as e:
        app.logger.error(f"Error processing Excel file: {e}")
        flash(f'Error processing file: {str(e)}', 'danger')
    
    return redirect(url_for('dashboard'))


@app.route('/export_data', methods=['GET'])
@login_required
def export_data():
    """Generate Excel template with actual data and improved formatting."""
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    # Get tenant data with readings using aggregation
    pipeline = [
        {"$match": {"admin_id": admin_id}},
        {"$lookup": {
            "from": "meter_readings",
            "let": {"tenant_id": "$_id"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$tenant_id", "$$tenant_id"]},
                    "admin_id": admin_id
                }},
                {"$sort": {"date_recorded": 1}}
            ],
            "as": "readings"
        }},
        {"$sort": {"name": 1}}
    ]
    
    tenants_with_readings = list(mongo.db.tenants.aggregate(pipeline))
    
    if not tenants_with_readings:
        # Create sample data if no tenants exist
        sample_date = datetime.now()
        tenant_data = [{
            'Name': 'Sample Tenant',
            'Phone': '+254712345678',
            'Date 1': sample_date.strftime('%Y-%m-%d'),
            'Reading 1': 0,
            'Date 2': (sample_date + timedelta(days=30)).strftime('%Y-%m-%d'),
            'Reading 2': 25
        }]
    else:
        # Process actual tenant data
        tenant_data = []
        max_readings = max(len(t['readings']) for t in tenants_with_readings) or 1
        
        for tenant in tenants_with_readings:
            row = {
                'Name': tenant['name'],
                'Phone': tenant['phone']
            }
            
            readings = tenant['readings']
            for i, reading in enumerate(readings, 1):
                date_str = reading['date_recorded'].strftime('%Y-%m-%d')
                row[f'Date {i}'] = date_str
                row[f'Reading {i}'] = reading['current_reading']
            
            # Fill empty reading slots
            if not readings:
                row['Date 1'] = datetime.now().strftime('%Y-%m-%d')
                row['Reading 1'] = 0
            
            tenant_data.append(row)
        
        # Ensure consistent column structure
        if tenant_data:
            max_readings = max(
                len([k for k in row.keys() if k.startswith('Reading')]) 
                for row in tenant_data
            )
    
    # Create DataFrame with consistent columns
    all_columns = ['Name', 'Phone']
    readings_count = max(
        len([k for k in row.keys() if k.startswith('Reading')]) 
        for row in tenant_data
    ) if tenant_data else 2
    
    for i in range(1, readings_count + 1):
        all_columns.extend([f'Date {i}', f'Reading {i}'])
    
    df = pd.DataFrame(tenant_data)
    
    # Add missing columns
    for col in all_columns:
        if col not in df.columns:
            df[col] = None
    
    df = df[all_columns]
    
    # Create Excel file with formatting
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Water Readings')
        
        # Get worksheet for formatting
        worksheet = writer.sheets['Water Readings']
        
        # Auto-adjust column widths
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            
            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[column_letter].width = adjusted_width
    
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name=f'water_billing_template_{datetime.now().strftime("%Y%m%d")}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/download_tenant_template')
@login_required
def download_tenant_template():
    # Create a sample DataFrame
    data = {
        'name': ['John Doe', 'Jane Smith'],
        'house_number': ['A1', 'B2'],
        'phone': ['0712345678', '0723456789'],
        'last_reading': [100.5, 85.2]
    }
    df = pd.DataFrame(data)
    
    # Create an Excel file in memory
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Tenants', index=False)
        
        # Get the xlsxwriter workbook and worksheet objects
        workbook = writer.book
        worksheet = writer.sheets['Tenants']
        
        # Add some formatting
        header_format = workbook.add_format({
            'bold': True,
            'text_wrap': True,
            'valign': 'top',
            'fg_color': '#D7E4BC',
            'border': 1
        })
        
        # Write the column headers with the defined format
        for col_num, value in enumerate(df.columns.values):
            worksheet.write(0, col_num, value, header_format)
            
        # Set column widths
        worksheet.set_column('A:A', 20)  # Name
        worksheet.set_column('B:B', 15)  # House Number
        worksheet.set_column('C:C', 15)  # Phone
        worksheet.set_column('D:D', 15)  # Last Reading
    
    # Reset the pointer to the start of the file
    output.seek(0)
    
    # Send the file
    return send_file(
        output,
        as_attachment=True,
        download_name='tenant_import_template.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

#download data of all of your tenants
# Route to download Excel template
if __name__ == '__main__':
    try:
        create_admin_user()
    except Exception as e:
        app.logger.error(f"Database initialization error: {e}")
        print(f"Database initialization error: {e}")
    
    app.run(debug=True, host='0.0.0.0', port=5000)
