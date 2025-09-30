import os
import logging
import urllib.parse
import re
import requests
import dns.resolver
from mpesa_integration import MpesaAPI
from subscription_config import SUBSCRIPTION_TIERS, MPESA_CONFIG
from crypto_utils import encrypt_mpesa_credentials, decrypt_mpesa_credentials
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from functools import wraps
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
import secrets
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman
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
import hashlib
import base64


    
# Load environment variables from the .env file
load_dotenv()

# Environment variables with fallbacks and validation
TALKSASA_API_KEY = os.environ.get("TALKSASA_API_KEY")
TALKSASA_SENDER_ID = os.environ.get("TALKSASA_SENDER_ID", "TALKSASA")

# Critical configuration - SECRET_KEY is required
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    import secrets as secret_gen
    SECRET_KEY = secret_gen.token_hex(32)
    print("WARNING: SECRET_KEY not set in environment. Using generated key for this session.")
    print("Please set SECRET_KEY in your .env file for production!")

# Parse numeric configuration with error handling
try:
    RATE_PER_UNIT = float(os.environ.get("RATE_PER_UNIT", 100))
except (ValueError, TypeError):
    RATE_PER_UNIT = 100.0
    print("WARNING: Invalid RATE_PER_UNIT, using default: 100")

try:
    DEFAULT_PER_PAGE = int(os.environ.get("DEFAULT_PER_PAGE", 10))
except (ValueError, TypeError):
    DEFAULT_PER_PAGE = 10
    print("WARNING: Invalid DEFAULT_PER_PAGE, using default: 10")

# MongoDB connection parameters - critical for app functionality
MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME")

if not MONGO_URI:
    print("ERROR: MONGO_URI not set in environment variables!")
    print("Please set MONGO_URI in your .env file")

if not DATABASE_NAME:
    print("ERROR: DATABASE_NAME not set in environment variables!")
    print("Please set DATABASE_NAME in your .env file")

# URL Shortening configuration
BITLY_ACCESS_TOKEN = os.getenv("BITLY_ACCESS_TOKEN")
ENABLE_URL_SHORTENING = os.getenv("ENABLE_URL_SHORTENING", "true").lower() == "true"

# Create Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY

# Enhanced CSRF Protection Configuration
app.config['WTF_CSRF_ENABLED'] = True
app.config['WTF_CSRF_TIME_LIMIT'] = 3600  # 1 hour token validity
app.config['WTF_CSRF_SSL_STRICT'] = False  # Allow HTTP for development
app.config['WTF_CSRF_CHECK_DEFAULT'] = True
app.config['WTF_CSRF_METHODS'] = ['POST', 'PUT', 'PATCH', 'DELETE']

# Initialize logging to file
handler = logging.FileHandler('app.log')
handler.setLevel(logging.INFO)
app.logger.addHandler(handler)

# Initialize CSRF Protection with enhanced configuration
csrf = CSRFProtect(app)
mongo = None

# CSP configuration - strict security policy
# Major templates now use external CSS/JS files for better security
csp = {
    'default-src': "'self'",
    'script-src': "'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://pagead2.googlesyndication.com",
    'style-src': "'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com",
    'img-src': "'self' data: https://via.placeholder.com",
    'font-src': "'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com data:",
    'connect-src': "'self' https://pagead2.googlesyndication.com",
    'frame-src': "'none'",
    'object-src': "'none'",
    'base-uri': "'self'",
    'form-action': "'self'",
    'upgrade-insecure-requests': True
}

cache_config = {
    "CACHE_TYPE": "SimpleCache",  # Use SimpleCache for in-memory caching
    "CACHE_DEFAULT_TIMEOUT": 3600  # Cache timeout in seconds (1 hour)
}
app.config.from_mapping(cache_config)
cache = Cache(app)
Talisman(app, content_security_policy=csp)

limiter = Limiter(key_func=get_remote_address, default_limits=["200 per day", "50 per hour"],app=app,storage_uri="memory://")

dns.resolver.default_resolver=dns.resolver.Resolver(configure=True)
dns.resolver.default_resolver.nameservers = ['8.8.8.8']
# Initialize MongoDB connection with better error handling
mongo = None
if MONGO_URI and DATABASE_NAME:
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
        print("Please check your MongoDB connection string and ensure MongoDB is running")
    except pymongo.errors.ConfigurationError as e:
        app.logger.error(f"MongoDB configuration error: {e}")
        print(f"MongoDB configuration error: {e}")
        print("Please check your MONGO_URI format")
    except Exception as e:
        app.logger.error(f"Database initialization error: {e}")
        print(f"Database initialization error: {e}")
        print("Please check your MongoDB configuration")
else:
    print("ERROR: Missing MongoDB configuration!")
    print("MONGO_URI and DATABASE_NAME must be set in environment variables")

if mongo is None:
    app.logger.critical("MongoDB did not initialize—check MONGO_URI and DATABASE_NAME!")
    print("CRITICAL: Failed to connect to MongoDB")
    print("The application cannot function without a database connection")
    print("Please:")
    print("1. Copy .env.example to .env")
    print("2. Set MONGO_URI and DATABASE_NAME in .env")
    print("3. Ensure MongoDB is running")
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

# Initialize M-Pesa API with error handling
mpesa = None
try:
    mpesa = MpesaAPI(
        consumer_key=os.getenv('MPESA_CONSUMER_KEY', MPESA_CONFIG.get('CONSUMER_KEY', '')),
        consumer_secret=os.getenv('MPESA_CONSUMER_SECRET', MPESA_CONFIG.get('CONSUMER_SECRET', '')),
        shortcode=os.getenv('MPESA_SHORTCODE', MPESA_CONFIG.get('SHORTCODE', '')),
        passkey=os.getenv('MPESA_PASSKEY', MPESA_CONFIG.get('PASSKEY', '')),
        env=os.getenv('MPESA_ENV', 'sandbox')
    )
    app.logger.info("M-Pesa API initialized successfully")
except Exception as e:
    app.logger.error(f"M-Pesa API initialization failed: {e}")
    print(f"WARNING: M-Pesa API initialization failed: {e}")
    print("Payment features may not work properly")

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
                "subscription_type": "monthly",  # monthly or annual
                "subscription_tier": "starter",
                "subscription_status": "active",
                "subscription_start_date": datetime.now(),
                "subscription_end_date": None,  # None for annual (set during payment)
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
                
                # Check expiry for monthly and annual subscriptions
                if subscription_type in ['monthly', 'annual']:
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
                    # Use total tenant count across all properties for subscription limits
                    current_count = get_total_tenant_count()
                    resource_name = 'tenants'
                elif resource_type == 'house':
                    max_allowed = tier_config['max_houses']
                    # Count houses across all properties
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

def initialize_properties_collection():
    """Create properties collection for multi-property support"""
    try:
        # Create properties collection if it doesn't exist
        if 'properties' not in mongo.db.list_collection_names():
            mongo.db.create_collection('properties')

        # Create indexes for properties
        mongo.db.properties.create_index([('admin_id', 1)])
        mongo.db.properties.create_index([('name', 1), ('admin_id', 1)])

        # Create default property for existing admins who don't have one
        admins_without_properties = mongo.db.admins.find({})
        for admin in admins_without_properties:
            # Check if admin already has a property
            existing_property = mongo.db.properties.find_one({"admin_id": admin["_id"]})
            if not existing_property:
                # Create default property
                default_property = {
                    "_id": ObjectId(),
                    "admin_id": admin["_id"],
                    "name": "Main Property",
                    "address": "",
                    "description": "Default property created automatically",
                    "created_at": datetime.now(),
                    "is_default": True,
                    "settings": {
                        "currency": "KES",
                        "billing_cycle": "monthly",
                        "water_rate_per_unit": RATE_PER_UNIT,
                        "billing": {
                            "enable_water_billing": True,
                            "enable_garbage_billing": False,
                            "garbage_rate": 0,
                            "late_payment_fee": 0,
                            "billing_day": 1  # Day of month when bills are generated
                        },
                        "payment_methods": {
                            "mpesa": {
                                "enabled": False,
                                "consumer_key": "",
                                "consumer_secret": "",
                                "shortcode": "",
                                "passkey": "",
                                "environment": "sandbox"  # sandbox or production
                            },
                            "cash": {
                                "enabled": True
                            },
                            "bank_transfer": {
                                "enabled": False,
                                "account_details": ""
                            }
                        }
                    }
                }
                property_result = mongo.db.properties.insert_one(default_property)
                property_id = property_result.inserted_id

                # Update existing tenants and houses to belong to this property
                mongo.db.tenants.update_many(
                    {"admin_id": admin["_id"], "property_id": {"$exists": False}},
                    {"$set": {"property_id": property_id}}
                )
                mongo.db.houses.update_many(
                    {"admin_id": admin["_id"], "property_id": {"$exists": False}},
                    {"$set": {"property_id": property_id}}
                )

        app.logger.info("Properties collection initialized")
        return True

    except Exception as e:
        app.logger.error(f"Error initializing properties collection: {e}")
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
        payment_type = request.form.get('payment_type', 'monthly')  # monthly or annual
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
        if payment_type == 'annual':
            amount = tier_config['annual_price']
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
                    "subscription_end_date": datetime.now() + relativedelta(months=1) if payment_type == 'monthly' else datetime.now() + relativedelta(years=1),
                    "auto_renew": True  # Both monthly and annual can have auto-renewal
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
                "auto_renew": True  # Both monthly and annual can have auto-renewal
            }
            
            if payment_type == 'monthly':
                subscription_update["subscription_end_date"] = datetime.now() + relativedelta(months=1)
                subscription_update["next_billing_date"] = datetime.now() + relativedelta(months=1)
            else:  # annual
                subscription_update["subscription_end_date"] = datetime.now() + relativedelta(years=1)
                subscription_update["next_billing_date"] = datetime.now() + relativedelta(years=1)
                
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

@app.route('/mpesa/till_callback', methods=['POST'])
@csrf.exempt  # M-Pesa callbacks can't send CSRF tokens
def mpesa_till_callback():
    """Handle automatic M-Pesa Till payments - redirects to main payment callback"""
    return mpesa_payment_callback()


def find_tenant_with_multi_reference(admin_id, payment_method, bill_ref_number, customer_phone, customer_name):
    """
    Enhanced tenant matching with multiple reference strategies
    Returns: (tenant, house, matching_method)
    """
    try:
        # Strategy 1: PayBill - House number as account reference (most reliable)
        if payment_method == 'paybill' and bill_ref_number:
            # Check if admin has enabled house number feature
            config = mongo.db.sms_config.find_one({"admin_id": admin_id})
            if config and config.get('use_house_number_as_account'):
                # Find house by house number
                house = mongo.db.houses.find_one({
                    "house_number": bill_ref_number,
                    "admin_id": admin_id
                })

                if house:
                    # Find tenant in that house
                    tenant = mongo.db.tenants.find_one({
                        "house_number": bill_ref_number,
                        "admin_id": admin_id
                    })

                    if tenant:
                        return tenant, house, "house_number_paybill"
                    else:
                        # House exists but no tenant found - still return house for manual allocation
                        return None, house, "house_found_no_tenant"

        # Strategy 2: Phone number + name matching (works for both PayBill and Till)
        if customer_phone and customer_name:
            # Normalize name for comparison (remove extra spaces, convert to lowercase)
            normalized_customer_name = ' '.join(customer_name.lower().split())

            # Find tenant by phone and name match
            tenants = list(mongo.db.tenants.find({
                "admin_id": admin_id,
                "phone": customer_phone
            }))

            for tenant in tenants:
                tenant_name = tenant.get('name', '').lower()
                normalized_tenant_name = ' '.join(tenant_name.split())

                # Check if names match (exact or partial)
                if (normalized_customer_name in normalized_tenant_name or
                    normalized_tenant_name in normalized_customer_name):

                    # Find the house for this tenant
                    house = mongo.db.houses.find_one({
                        "house_number": tenant.get('house_number'),
                        "admin_id": admin_id
                    })

                    return tenant, house, "phone_and_name_match"

        # Strategy 3: Phone number only (less reliable)
        if customer_phone:
            tenant = mongo.db.tenants.find_one({
                "admin_id": admin_id,
                "phone": customer_phone
            })

            if tenant:
                house = mongo.db.houses.find_one({
                    "house_number": tenant.get('house_number'),
                    "admin_id": admin_id
                })

                return tenant, house, "phone_only_match"

        # Strategy 4: Till users - House number as tenant reference (if available)
        if payment_method == 'till' and bill_ref_number:
            # For Till users, sometimes bill_ref_number might be house number
            house = mongo.db.houses.find_one({
                "house_number": bill_ref_number,
                "admin_id": admin_id
            })

            if house:
                tenant = mongo.db.tenants.find_one({
                    "house_number": bill_ref_number,
                    "admin_id": admin_id
                })

                return tenant, house, "house_number_till"

        # Strategy 5: Name matching only (least reliable)
        if customer_name and len(customer_name.strip()) > 3:
            normalized_customer_name = ' '.join(customer_name.lower().split())

            # Find tenants with similar names
            tenants = list(mongo.db.tenants.find({"admin_id": admin_id}))

            for tenant in tenants:
                tenant_name = tenant.get('name', '').lower()
                normalized_tenant_name = ' '.join(tenant_name.split())

                # More strict name matching for this fallback
                if normalized_customer_name == normalized_tenant_name:
                    house = mongo.db.houses.find_one({
                        "house_number": tenant.get('house_number'),
                        "admin_id": admin_id
                    })

                    return tenant, house, "name_only_exact_match"

        # No match found
        return None, None, "no_match_found"

    except Exception as e:
        app.logger.error(f"Error in tenant matching: {str(e)}")
        return None, None, "matching_error"

def get_matching_confidence(matching_method):
    """Return confidence level for different matching methods"""
    confidence_map = {
        "house_number_paybill": "high",      # Most reliable - house number + PayBill
        "phone_and_name_match": "high",      # High confidence - phone + name match
        "house_number_till": "medium",       # Medium - house number but via Till
        "phone_only_match": "medium",        # Medium - phone only
        "house_found_no_tenant": "low",     # Low - house found but no tenant
        "name_only_exact_match": "low",     # Low - name only
        "no_match_found": "none",           # No match
        "matching_error": "error"           # Error in matching
    }
    return confidence_map.get(matching_method, "unknown")

@app.route('/mpesa/paybill_callback', methods=['POST'])
@csrf.exempt  # M-Pesa callbacks can't send CSRF tokens
def mpesa_payment_callback():
    """Handle automatic M-Pesa PayBill/Till payments with multi-reference tenant matching"""
    try:
        # Log the incoming request
        data = request.get_json()
        app.logger.info(f"PayBill callback received: {data}")

        # Extract payment information from PayBill callback
        # PayBill format is different from STK Push
        trans_type = data.get('TransactionType')
        trans_id = data.get('TransID')
        trans_time = data.get('TransTime')
        trans_amount = data.get('TransAmount')
        business_short_code = data.get('BusinessShortCode')
        bill_ref_number = data.get('BillRefNumber', '').strip()  # This is the account name/house number
        invoice_number = data.get('InvoiceNumber')
        org_account_balance = data.get('OrgAccountBalance')
        third_party_trans_id = data.get('ThirdPartyTransID')
        msisdn = data.get('MSISDN')  # Customer phone number
        first_name = data.get('FirstName', '')
        middle_name = data.get('MiddleName', '')
        last_name = data.get('LastName', '')

        # Validate required fields (trans_amount and trans_id are always required)
        if not all([trans_id, trans_amount]):
            app.logger.error("Missing required payment fields")
            return jsonify({
                'ResultCode': 'C00000001',
                'ResultDesc': 'Missing required fields'
            })

        # Find admin by business short code (supports both PayBill and Till)
        admin = mongo.db.admins.find_one({
            '$or': [
                {'business_number': str(business_short_code)},
                {'till': str(business_short_code)}
            ]
        })

        if not admin:
            app.logger.error(f"Admin not found for business code: {business_short_code}")
            return jsonify({
                'ResultCode': 'C00000001',
                'ResultDesc': 'Business not found'
            })

        admin_id = admin['_id']
        payment_method = admin.get('payment_method', 'till')  # Default to till for older accounts

        # Clean phone number (remove country code if present)
        customer_phone = msisdn
        if customer_phone and customer_phone.startswith('254'):
            customer_phone = '0' + customer_phone[3:]

        # Build customer name from M-Pesa data
        customer_name = f"{first_name} {middle_name} {last_name}".strip()

        # Enhanced tenant matching with multiple strategies
        tenant, house, matching_method = find_tenant_with_multi_reference(
            admin_id=admin_id,
            payment_method=payment_method,
            bill_ref_number=bill_ref_number,
            customer_phone=customer_phone,
            customer_name=customer_name
        )

        # Create automatic payment record
        current_month = datetime.now().strftime('%Y-%m')

        # Check if payment already exists for this transaction
        existing_payment = mongo.db.payments.find_one({
            'mpesa_trans_id': trans_id,
            'admin_id': admin_id
        })

        if existing_payment:
            app.logger.info(f"Payment already recorded for transaction: {trans_id}")
            return jsonify({
                'ResultCode': '00000000',
                'ResultDesc': 'Success - payment already recorded'
            })

        # Create unallocated payment record (to be manually allocated by admin)
        unallocated_payment = {
            'admin_id': admin_id,
            'house_id': house['_id'] if house else None,
            'house_number': house.get('house_number') if house else bill_ref_number,
            'tenant_id': tenant['_id'] if tenant else None,
            'tenant_name': tenant['name'] if tenant else customer_name,
            'amount_received': float(trans_amount),
            'amount_allocated': 0.0,
            'amount_remaining': float(trans_amount),
            'payment_method': f'mpesa_{payment_method}',  # mpesa_paybill or mpesa_till
            'payment_status': 'unallocated',
            'allocation_status': 'pending',  # pending/partial/complete
            'payment_date': datetime.now(),
            'month_year': current_month,
            'mpesa_trans_id': trans_id,
            'mpesa_phone': msisdn,
            'customer_phone': customer_phone,
            'customer_name': customer_name,
            'bill_ref_number': bill_ref_number,
            'matching_method': matching_method,
            'matching_confidence': get_matching_confidence(matching_method),
            'allocations': [],  # Array to track bill allocations
            'created_at': datetime.now(),
            'updated_at': datetime.now(),
            'notes': f'Auto M-Pesa payment via {payment_method.upper()} - {matching_method}. Ref: {bill_ref_number or "N/A"}'
        }

        # Insert unallocated payment record
        result = mongo.db.unallocated_payments.insert_one(unallocated_payment)
        app.logger.info(f"Unallocated payment recorded: {result.inserted_id}")

        # Send confirmation SMS to tenant if phone number available
        if tenant and tenant.get('phone'):
            try:
                house_info = house.get('house_number', 'N/A') if house else 'N/A'
                confidence = get_matching_confidence(matching_method)
                message = f"Payment received! Amount: KES {trans_amount}, House: {house_info}, Ref: {trans_id}. Your payment is being processed. Thank you!"
                send_message(tenant['phone'], message)
            except Exception as sms_error:
                app.logger.error(f"SMS sending failed: {sms_error}")

        # Send notification to admin about unallocated payment with matching details
        if admin.get('phone'):
            try:
                tenant_name = tenant['name'] if tenant else customer_name or 'Unknown'
                house_info = house.get('house_number') if house else bill_ref_number or 'Unknown'
                confidence = get_matching_confidence(matching_method)

                # Create detailed message based on matching confidence
                if confidence == "high":
                    message = f"NEW PAYMENT (High Match): {tenant_name}, House {house_info}, KES {trans_amount}. Tenant matched via {payment_method.upper()}. Ref: {trans_id}"
                elif confidence == "medium":
                    message = f"NEW PAYMENT (Medium Match): {tenant_name}, House {house_info}, KES {trans_amount}. Please verify and allocate. Ref: {trans_id}"
                elif confidence == "low":
                    message = f"NEW PAYMENT (Low Match): {tenant_name}, House {house_info}, KES {trans_amount}. Uncertain match - please verify. Ref: {trans_id}"
                else:
                    message = f"NEW PAYMENT (No Match): {customer_name}, KES {trans_amount}. Could not match tenant. Please allocate manually. Ref: {trans_id}"

                send_message(admin['phone'], message)
            except Exception as sms_error:
                app.logger.error(f"Admin SMS sending failed: {sms_error}")

        return jsonify({
            'ResultCode': '00000000',
            'ResultDesc': 'Success'
        })

    except Exception as e:
        app.logger.error(f"PayBill callback error: {e}")
        return jsonify({
            'ResultCode': 'C00000001',
            'ResultDesc': 'Internal server error'
        })


@app.route('/mpesa/validation', methods=['POST'])
@csrf.exempt
def mpesa_validation():
    """M-Pesa validation endpoint - always accept payments"""
    try:
        # Log validation request
        data = request.get_json()
        app.logger.info(f"M-Pesa validation request: {data}")

        # Always return success to allow payments
        return jsonify({
            'ResultCode': '00000000',
            'ResultDesc': 'Success'
        })

    except Exception as e:
        app.logger.error(f"M-Pesa validation error: {e}")
        return jsonify({
            'ResultCode': '00000000',
            'ResultDesc': 'Success'  # Always allow payments even if validation fails
        })


def get_mpesa_credentials(admin_id):
    """Get decrypted M-Pesa credentials for an admin"""
    try:
        encrypted_config = mongo.db.mpesa_config.find_one({"admin_id": admin_id})
        if not encrypted_config:
            return None

        return decrypt_mpesa_credentials(encrypted_config)
    except Exception as e:
        app.logger.error(f"Error getting M-Pesa credentials: {e}")
        return None


def generate_tenant_access_token(tenant_id, admin_id, expires_in_hours=24):
    """Generate a secure access token for tenant portal"""
    token_data = {
        'tenant_id': str(tenant_id),
        'admin_id': str(admin_id),
        'timestamp': datetime.now().isoformat(),
        'expires': (datetime.now() + timedelta(hours=expires_in_hours)).isoformat()
    }

    serializer = URLSafeTimedSerializer(SECRET_KEY)
    token = serializer.dumps(token_data)

    # Store token in database for tracking
    mongo.db.tenant_access_tokens.insert_one({
        'token': token,
        'tenant_id': ObjectId(tenant_id),
        'admin_id': ObjectId(admin_id),
        'created_at': datetime.now(),
        'expires_at': datetime.now() + timedelta(hours=expires_in_hours),
        'used': False
    })

    return token


def verify_tenant_access_token(token):
    """Verify and decode tenant access token"""
    try:
        serializer = URLSafeTimedSerializer(SECRET_KEY)
        token_data = serializer.loads(token, max_age=24*3600)  # 24 hours

        # Check if token exists in database and is not used
        token_record = mongo.db.tenant_access_tokens.find_one({
            'token': token,
            'used': False,
            'expires_at': {'$gt': datetime.now()}
        })

        if not token_record:
            app.logger.warning(f"Token not found in database or expired: {token[:20]}...")
            # If token not in DB but signature is valid, still allow access for backward compatibility
            # but add logging to track this
            app.logger.info(f"Allowing access with signature-only verification for token: {token[:20]}...")

        return {
            'tenant_id': ObjectId(token_data['tenant_id']),
            'admin_id': ObjectId(token_data['admin_id'])
        }

    except SignatureExpired:
        app.logger.warning(f"Token signature expired: {token[:20]}...")
        return None
    except BadSignature:
        app.logger.warning(f"Invalid token signature: {token[:20]}...")
        return None
    except Exception as e:
        app.logger.error(f"Token verification error: {e}")
        return None


def mark_token_as_used(token):
    """Mark a token as used to prevent reuse"""
    mongo.db.tenant_access_tokens.update_one(
        {'token': token},
        {'$set': {'used': True, 'used_at': datetime.now()}}
    )


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
    """Toggle auto-renewal for monthly and annual subscriptions"""
    try:
        admin_id = get_admin_id()
        admin = mongo.db.admins.find_one({"_id": admin_id})

        subscription_type = admin.get('subscription_type')
        if subscription_type not in ['monthly', 'annual']:
            return jsonify({'error': 'Auto-renewal only applies to monthly and annual subscriptions'}), 400
            
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
                # Create shortened URL for subscription renewal
                long_subscription_url = f"{request.url_root.rstrip('/')}/subscription"
                short_subscription_url = shorten_url(long_subscription_url, f"sub_{admin['_id']}_{datetime.now().strftime('%Y%m')}")

                days_remaining = (admin['subscription_end_date'] - datetime.now()).days
                message = f"Your {SUBSCRIPTION_TIERS[admin['subscription_tier']]['name']} subscription expires in {days_remaining} days. Renew now: {short_subscription_url}"
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

def send_payment_reminders():
    """Send automated payment reminders to tenants with overdue bills"""
    try:
        # Find bills that are overdue (unpaid bills where due_date is past)
        overdue_cutoff = datetime.now() - timedelta(days=1)

        overdue_bills = list(mongo.db.payments.find({
            'payment_status': {'$in': ['unpaid', 'partial']},
            'due_date': {'$lt': overdue_cutoff},
            'reminder_sent': {'$ne': True}  # Only send reminder once
        }))

        reminder_count = 0

        for bill in overdue_bills:
            try:
                # Get tenant and admin info
                tenant = mongo.db.tenants.find_one({'_id': bill['tenant_id']})
                admin = mongo.db.admins.find_one({'_id': bill['admin_id']})

                if not tenant or not admin or not tenant.get('phone'):
                    continue

                # Get property-specific payment info
                property_id = bill.get('property_id')
                payment_info = get_property_payment_info(admin, property_id)

                # Calculate overdue amount
                outstanding_amount = bill['bill_amount'] - bill.get('amount_paid', 0)
                days_overdue = (datetime.now() - bill['due_date']).days

                # Calculate late payment fine
                property_settings = get_property_billing_settings(bill.get('property_id'), bill['admin_id'])
                fine_amount = calculate_late_payment_fine(bill, property_settings)
                total_due = outstanding_amount + fine_amount

                # Generate short tenant portal link
                access_token = generate_tenant_access_token(tenant['_id'], bill['admin_id'], expires_in_hours=72)
                long_portal_link = f"{request.url_root}tenant_portal/{access_token}"
                portal_link = shorten_url(long_portal_link, f"reminder_{tenant['_id']}_{datetime.now().strftime('%Y%m%d')}")

                # Create reminder message with fine information
                bill_type = bill.get('bill_type', 'Water').title()
                if fine_amount > 0:
                    message = (
                        f"Payment Reminder: {tenant['name']}, your {bill_type} bill of KES {outstanding_amount:.2f} "
                        f"is {days_overdue} days overdue. Late fee: KES {fine_amount:.2f}. "
                        f"Total due: KES {total_due:.2f}. {payment_info} "
                        f"View details: {portal_link} "
                        f"Contact: {admin.get('phone', 'N/A')}"
                    )
                else:
                    message = (
                        f"Payment Reminder: {tenant['name']}, your {bill_type} bill of KES {outstanding_amount:.2f} "
                        f"is {days_overdue} days overdue. {payment_info} "
                        f"View bill details: {portal_link} "
                        f"Contact: {admin.get('phone', 'N/A')}"
                    )

                # Send SMS
                response = send_message(tenant['phone'], message)

                if "error" not in response:
                    # Mark reminder as sent
                    mongo.db.payments.update_one(
                        {'_id': bill['_id']},
                        {
                            '$set': {
                                'reminder_sent': True,
                                'reminder_sent_date': datetime.now()
                            }
                        }
                    )
                    reminder_count += 1
                    app.logger.info(f"Payment reminder sent to {tenant['name']} for bill {bill['_id']}")
                else:
                    app.logger.error(f"Failed to send reminder to {tenant['name']}: {response.get('error', 'Unknown error')}")

            except Exception as tenant_error:
                app.logger.error(f"Error sending reminder for bill {bill['_id']}: {str(tenant_error)}")
                continue

        app.logger.info(f"Sent {reminder_count} payment reminders")
        return reminder_count

    except Exception as e:
        app.logger.error(f"Error in send_payment_reminders: {str(e)}")
        return 0

def get_property_payment_info(admin, property_id=None):
    """Get property-specific payment information for SMS"""
    try:
        # Get property-specific payment config if available
        if property_id:
            property_doc = mongo.db.properties.find_one({'_id': property_id})
            if property_doc and property_doc.get('payment_methods', {}).get('mpesa', {}).get('enabled'):
                mpesa_config = property_doc['payment_methods']['mpesa']
                if mpesa_config.get('shortcode'):
                    return f"Pay via PayBill: {mpesa_config['shortcode']}"

        # Fall back to admin-level payment config
        if admin.get('payment_method') == 'till' and admin.get('till'):
            return f"Pay via Till: {admin['till']}"
        elif admin.get('payment_method') == 'paybill' and admin.get('business_number'):
            account_name = admin.get('account_name', admin.get('name', 'Main'))
            return f"Pay via PayBill: {admin['business_number']}, Account: {account_name}"
        else:
            # Fallback
            till_number = admin.get('till', 'N/A')
            return f"Pay via Till: {till_number}"

    except Exception as e:
        app.logger.error(f"Error getting payment info: {str(e)}")
        return "Contact landlord for payment details"

@app.route('/send_payment_reminders')
@login_required
def manual_payment_reminders():
    """Manual trigger for payment reminders (admin only)"""
    try:
        admin_id = get_admin_id()
        admin = mongo.db.admins.find_one({'_id': admin_id})

        if not admin:
            flash('Admin not found', 'danger')
            return redirect(url_for('dashboard'))

        # Send reminders for this admin's tenants only
        overdue_cutoff = datetime.now() - timedelta(days=1)

        overdue_bills = list(mongo.db.payments.find({
            'admin_id': admin_id,
            'payment_status': {'$in': ['unpaid', 'partial']},
            'due_date': {'$lt': overdue_cutoff}
        }))

        reminder_count = 0

        for bill in overdue_bills:
            try:
                tenant = mongo.db.tenants.find_one({'_id': bill['tenant_id']})

                if not tenant or not tenant.get('phone'):
                    continue

                # Get property-specific payment info
                property_id = bill.get('property_id')
                payment_info = get_property_payment_info(admin, property_id)

                outstanding_amount = bill['bill_amount'] - bill.get('amount_paid', 0)
                days_overdue = (datetime.now() - bill['due_date']).days

                # Calculate late payment fine
                property_settings = get_property_billing_settings(property_id, admin_id)
                fine_amount = calculate_late_payment_fine(bill, property_settings)
                total_due = outstanding_amount + fine_amount

                # Generate short tenant portal link
                access_token = generate_tenant_access_token(tenant['_id'], admin_id, expires_in_hours=72)
                long_portal_link = f"{request.url_root}tenant_portal/{access_token}"
                portal_link = shorten_url(long_portal_link, f"manual_{tenant['_id']}_{datetime.now().strftime('%Y%m%d%H')}")

                bill_type = bill.get('bill_type', 'Water').title()
                if fine_amount > 0:
                    message = (
                        f"Payment Reminder: {tenant['name']}, your {bill_type} bill of KES {outstanding_amount:.2f} "
                        f"is {days_overdue} days overdue. Late fee: KES {fine_amount:.2f}. "
                        f"Total due: KES {total_due:.2f}. {payment_info} "
                        f"View details: {portal_link} "
                        f"Contact: {admin.get('phone', 'N/A')}"
                    )
                else:
                    message = (
                        f"Payment Reminder: {tenant['name']}, your {bill_type} bill of KES {outstanding_amount:.2f} "
                        f"is {days_overdue} days overdue. {payment_info} "
                        f"View details: {portal_link} "
                        f"Contact: {admin.get('phone', 'N/A')}"
                    )

                response = send_message(tenant['phone'], message)

                if "error" not in response:
                    mongo.db.payments.update_one(
                        {'_id': bill['_id']},
                        {
                            '$set': {
                                'manual_reminder_sent': True,
                                'manual_reminder_sent_date': datetime.now()
                            }
                        }
                    )
                    reminder_count += 1

            except Exception as tenant_error:
                app.logger.error(f"Error sending manual reminder for bill {bill['_id']}: {str(tenant_error)}")
                continue

        flash(f'Sent {reminder_count} payment reminders successfully', 'success')
        return redirect(url_for('payments_dashboard'))

    except Exception as e:
        app.logger.error(f"Error in manual payment reminders: {str(e)}")
        flash('Error sending payment reminders', 'danger')
        return redirect(url_for('dashboard'))


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

def get_current_property_id():
    """Get current property ID from session or return default property."""
    try:
        admin_id = get_admin_id()

        # If property_id is in session, use it
        if 'property_id' in session:
            return ObjectId(session['property_id'])

        # Otherwise, get the default property for this admin
        default_property = mongo.db.properties.find_one({
            "admin_id": admin_id,
            "is_default": True
        })

        if default_property:
            session['property_id'] = str(default_property['_id'])
            return default_property['_id']

        # If no default property exists, get the first one
        first_property = mongo.db.properties.find_one({"admin_id": admin_id})
        if first_property:
            session['property_id'] = str(first_property['_id'])
            return first_property['_id']

        # If no properties exist, create one
        new_property = {
            "_id": ObjectId(),
            "admin_id": admin_id,
            "name": "Main Property",
            "address": "",
            "description": "Default property",
            "created_at": datetime.now(),
            "is_default": True,
            "settings": {
                "currency": "KES",
                "billing_cycle": "monthly"
            }
        }
        property_result = mongo.db.properties.insert_one(new_property)
        session['property_id'] = str(property_result.inserted_id)
        return property_result.inserted_id

    except Exception as e:
        app.logger.error(f"Error getting current property: {e}")
        raise ValueError("Invalid property session")

def get_user_properties():
    """Get all properties for the current admin."""
    try:
        admin_id = get_admin_id()
        properties = list(mongo.db.properties.find({"admin_id": admin_id}).sort("name", 1))
        return properties
    except Exception as e:
        app.logger.error(f"Error getting user properties: {e}")
        return []

def get_total_tenant_count():
    """Get total tenant count across all properties for subscription calculation."""
    try:
        admin_id = get_admin_id()
        total_count = mongo.db.tenants.count_documents({"admin_id": admin_id})
        return total_count
    except Exception as e:
        app.logger.error(f"Error getting total tenant count: {e}")
        return 0

def validate_property_access(property_id, admin_id=None):
    """
    Validate that the admin has access to the specified property.
    CRITICAL for preventing cross-property data access.
    """
    try:
        if not admin_id:
            admin_id = get_admin_id()

        if not property_id:
            return False

        # Ensure property belongs to the admin
        property_exists = mongo.db.properties.find_one({
            "_id": ObjectId(property_id),
            "admin_id": admin_id
        })

        return property_exists is not None

    except Exception as e:
        app.logger.error(f"Error validating property access: {e}")
        return False

def validate_resource_property_access(resource_type, resource_id, admin_id=None, property_id=None):
    """
    Validate that a resource (tenant, house, reading) belongs to the current property.
    CRITICAL for data separation security.
    """
    try:
        if not admin_id:
            admin_id = get_admin_id()

        if not property_id:
            property_id = get_current_property_id()

        if not property_id:
            return False

        collection_map = {
            'tenant': 'tenants',
            'house': 'houses',
            'reading': 'meter_readings'
        }

        if resource_type not in collection_map:
            return False

        collection = getattr(mongo.db, collection_map[resource_type])

        # Check if resource exists and belongs to admin and property
        resource = collection.find_one({
            "_id": ObjectId(resource_id),
            "admin_id": admin_id,
            "property_id": property_id
        })

        return resource is not None

    except Exception as e:
        app.logger.error(f"Error validating resource property access: {e}")
        return False

def get_property_settings(property_id=None):
    """Get billing settings for a specific property."""
    try:
        if not property_id:
            property_id = get_current_property_id()

        admin_id = get_admin_id()
        property_doc = mongo.db.properties.find_one({
            "_id": ObjectId(property_id),
            "admin_id": admin_id
        })

        if property_doc and 'settings' in property_doc:
            return property_doc['settings']

        # Return default settings if none found
        return {
            "currency": "KES",
            "billing_cycle": "monthly",
            "water_rate_per_unit": RATE_PER_UNIT,
            "billing": {
                "enable_water_billing": True,
                "enable_garbage_billing": False,
                "garbage_rate": 0,
                "late_payment_fee": 0,
                "billing_day": 1
            },
            "payment_methods": {
                "mpesa": {"enabled": False},
                "cash": {"enabled": True},
                "bank_transfer": {"enabled": False}
            }
        }
    except Exception as e:
        app.logger.error(f"Error getting property settings: {e}")
        return None

def get_property_water_rate(property_id=None):
    """Get water rate for specific property."""
    try:
        settings = get_property_settings(property_id)
        if settings:
            return settings.get('water_rate_per_unit', RATE_PER_UNIT)
        return RATE_PER_UNIT
    except Exception as e:
        app.logger.error(f"Error getting property water rate: {e}")
        return RATE_PER_UNIT

def get_property_mpesa_config(property_id=None):
    """Get M-Pesa configuration for specific property."""
    try:
        settings = get_property_settings(property_id)
        if settings and 'payment_methods' in settings:
            mpesa_config = settings['payment_methods'].get('mpesa', {})
            if mpesa_config.get('enabled', False):
                return mpesa_config

        # Fall back to admin-level M-Pesa config
        admin_id = get_admin_id()
        admin_mpesa = mongo.db.mpesa_config.find_one({"admin_id": admin_id})
        if admin_mpesa:
            return decrypt_mpesa_credentials(admin_mpesa)

        return None
    except Exception as e:
        app.logger.error(f"Error getting property M-Pesa config: {e}")
        return None

def get_property_mpesa_api(property_id=None):
    """Get property-specific M-Pesa API instance."""
    try:
        config = get_property_mpesa_config(property_id)
        if config and config.get('consumer_key') and config.get('consumer_secret'):
            return MpesaAPI(
                consumer_key=config.get('consumer_key'),
                consumer_secret=config.get('consumer_secret'),
                shortcode=config.get('shortcode'),
                passkey=config.get('passkey'),
                env=config.get('environment', 'sandbox')
            )

        # Fall back to default M-Pesa API
        return mpesa

    except Exception as e:
        app.logger.error(f"Error creating property M-Pesa API: {e}")
        return mpesa  # Return default as fallback

def shorten_url_bitly(long_url):
    """Shorten URL using Bitly API."""
    try:
        if not BITLY_ACCESS_TOKEN:
            return None

        import requests

        headers = {
            'Authorization': f'Bearer {BITLY_ACCESS_TOKEN}',
            'Content-Type': 'application/json',
        }

        data = {
            'long_url': long_url,
            'domain': 'bit.ly'  # You can use custom domains if you have them
        }

        response = requests.post('https://api-ssl.bitly.com/v4/shorten',
                               json=data, headers=headers)

        if response.status_code == 200 or response.status_code == 201:
            result = response.json()
            return result.get('link')
        else:
            app.logger.error(f"Bitly API error: {response.status_code} - {response.text}")
            return None

    except Exception as e:
        app.logger.error(f"Error shortening URL with Bitly: {e}")
        return None

def create_custom_short_url(long_url, identifier=None):
    """Create a custom short URL using our own database."""
    try:
        # Generate short code
        if identifier:
            # Use provided identifier for consistent URLs
            short_code = hashlib.md5(identifier.encode()).hexdigest()[:8]
        else:
            # Generate random short code
            short_code = hashlib.md5(long_url.encode()).hexdigest()[:8]

        # Check if short URL already exists
        existing = mongo.db.short_urls.find_one({"short_code": short_code})
        if existing:
            return f"{request.url_root}s/{short_code}"

        # Store in database
        short_url_doc = {
            "short_code": short_code,
            "long_url": long_url,
            "created_at": datetime.now(),
            "click_count": 0,
            "identifier": identifier
        }

        mongo.db.short_urls.insert_one(short_url_doc)
        return f"{request.url_root}s/{short_code}"

    except Exception as e:
        app.logger.error(f"Error creating custom short URL: {e}")
        return long_url  # Return original URL as fallback

def shorten_url(long_url, identifier=None):
    """Shorten URL using preferred method."""
    try:
        if not ENABLE_URL_SHORTENING:
            return long_url

        # Try Bitly first if configured
        if BITLY_ACCESS_TOKEN:
            short_url = shorten_url_bitly(long_url)
            if short_url:
                return short_url

        # Fallback to custom shortener
        return create_custom_short_url(long_url, identifier)

    except Exception as e:
        app.logger.error(f"Error in URL shortening: {e}")
        return long_url  # Return original URL as fallback



# Property Management Routes
@app.route('/properties',methods = ['GET','POST'])
@login_required
def properties():
    """Display all properties for the current admin"""
    try:
        admin_id = get_admin_id()
        properties = get_user_properties()
        current_property_id = get_current_property_id()

        # Get tenant counts for each property
        for prop in properties:
            prop['tenant_count'] = mongo.db.tenants.count_documents({
                "admin_id": admin_id,
                "property_id": prop['_id']
            })
            prop['house_count'] = mongo.db.houses.count_documents({
                "admin_id": admin_id,
                "property_id": prop['_id']
            })

        total_tenants = get_total_tenant_count()

        return render_template('properties.html',
                             properties=properties,
                             current_property_id=current_property_id,
                             total_tenants=total_tenants)

    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    except Exception as e:
        app.logger.error(f"Error in properties route: {e}")
        flash('An error occurred while loading properties.', 'danger')
        return redirect(url_for('dashboard'))

@app.route('/switch_property/<property_id>', methods=['POST'])
@login_required
def switch_property(property_id):
    """Switch to a different property"""
    try:
        admin_id = get_admin_id()
        property_id_obj = ObjectId(property_id)

        # Verify property belongs to current admin
        property_doc = mongo.db.properties.find_one({
            "_id": property_id_obj,
            "admin_id": admin_id
        })

        if not property_doc:
            flash('Property not found or access denied.', 'danger')
            return redirect(url_for('properties'))

        # Update session
        session['property_id'] = str(property_id_obj)
        flash(f'Switched to {property_doc["name"]}', 'success')

        return redirect(url_for('dashboard'))

    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    except Exception as e:
        app.logger.error(f"Error switching property: {e}")
        flash('Error switching property.', 'danger')
        return redirect(url_for('properties'))

@app.route('/add_property', methods=['POST'])
@login_required
def add_property():
    """Add a new property"""
    try:
        admin_id = get_admin_id()

        name = request.form.get('name', '').strip()
        address = request.form.get('address', '').strip()
        description = request.form.get('description', '').strip()

        if not name:
            flash('Property name is required.', 'danger')
            return redirect(url_for('properties'))

        # Check for duplicate property name
        existing = mongo.db.properties.find_one({
            "admin_id": admin_id,
            "name": name
        })

        if existing:
            flash('Property name already exists.', 'danger')
            return redirect(url_for('properties'))

        # Create new property
        new_property = {
            "_id": ObjectId(),
            "admin_id": admin_id,
            "name": name,
            "address": address,
            "description": description,
            "created_at": datetime.now(),
            "is_default": False,
            "settings": {
                "currency": "KES",
                "billing_cycle": "monthly"
            }
        }

        mongo.db.properties.insert_one(new_property)
        flash(f'Property "{name}" created successfully!', 'success')

        return redirect(url_for('properties'))

    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    except Exception as e:
        app.logger.error(f"Error adding property: {e}")
        flash('Error creating property.', 'danger')
        return redirect(url_for('properties'))

@app.route('/edit_property/<property_id>', methods=['POST'])
@login_required
def edit_property(property_id):
    """Edit property details"""
    try:
        admin_id = get_admin_id()
        property_id_obj = ObjectId(property_id)

        # Verify property belongs to current admin
        property_doc = mongo.db.properties.find_one({
            "_id": property_id_obj,
            "admin_id": admin_id
        })

        if not property_doc:
            flash('Property not found or access denied.', 'danger')
            return redirect(url_for('properties'))

        name = request.form.get('name', '').strip()
        address = request.form.get('address', '').strip()
        description = request.form.get('description', '').strip()

        if not name:
            flash('Property name is required.', 'danger')
            return redirect(url_for('properties'))

        # Check for duplicate name (excluding current property)
        existing = mongo.db.properties.find_one({
            "admin_id": admin_id,
            "name": name,
            "_id": {"$ne": property_id_obj}
        })

        if existing:
            flash('Property name already exists.', 'danger')
            return redirect(url_for('properties'))

        # Update property
        mongo.db.properties.update_one(
            {"_id": property_id_obj},
            {"$set": {
                "name": name,
                "address": address,
                "description": description,
                "updated_at": datetime.now()
            }}
        )

        flash(f'Property "{name}" updated successfully!', 'success')
        return redirect(url_for('properties'))

    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    except Exception as e:
        app.logger.error(f"Error editing property: {e}")
        flash('Error updating property.', 'danger')
        return redirect(url_for('properties'))

@app.route('/property_settings/<property_id>')
@login_required
def property_settings(property_id):
    """Property-specific billing and payment settings"""
    try:
        admin_id = get_admin_id()
        property_id_obj = ObjectId(property_id)

        # Verify property belongs to current admin
        property_doc = mongo.db.properties.find_one({
            "_id": property_id_obj,
            "admin_id": admin_id
        })

        if not property_doc:
            flash('Property not found or access denied.', 'danger')
            return redirect(url_for('properties'))

        # Get current settings
        settings = get_property_settings(property_id)

        return render_template('property_settings.html',
                             property=property_doc,
                             settings=settings)

    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    except Exception as e:
        app.logger.error(f"Error loading property settings: {e}")
        flash('Error loading property settings.', 'danger')
        return redirect(url_for('properties'))

@app.route('/update_property_settings/<property_id>', methods=['POST','GET'])
@login_required
def update_property_settings(property_id):
    """Update property-specific billing settings"""
    try:
        admin_id = get_admin_id()
        property_id_obj = ObjectId(property_id)

        # Verify property belongs to current admin
        property_doc = mongo.db.properties.find_one({
            "_id": property_id_obj,
            "admin_id": admin_id
        })

        if not property_doc:
            flash('Property not found or access denied.', 'danger')
            return redirect(url_for('properties'))

        # Extract form data
        water_rate = float(request.form.get('water_rate_per_unit', RATE_PER_UNIT))
        enable_water_billing = 'enable_water_billing' in request.form
        enable_garbage_billing = 'enable_garbage_billing' in request.form
        garbage_rate = float(request.form.get('garbage_rate', 0))
        late_payment_fee = float(request.form.get('late_payment_fee', 0))
        billing_day = int(request.form.get('billing_day', 1))

        # Late payment fine settings
        enable_late_payment_fines = 'enable_late_payment_fines' in request.form
        grace_period_days = int(request.form.get('grace_period_days', 7))
        fine_type = request.form.get('fine_type', 'percentage')
        fine_rate = float(request.form.get('fine_rate', 5))
        fine_frequency = request.form.get('fine_frequency', 'one_time')
        max_fine_amount = float(request.form.get('max_fine_amount', 0))

        # M-Pesa settings
        mpesa_enabled = 'mpesa_enabled' in request.form
        mpesa_consumer_key = request.form.get('mpesa_consumer_key', '').strip()
        mpesa_consumer_secret = request.form.get('mpesa_consumer_secret', '').strip()
        mpesa_shortcode = request.form.get('mpesa_shortcode', '').strip()
        mpesa_passkey = request.form.get('mpesa_passkey', '').strip()
        mpesa_environment = request.form.get('mpesa_environment', 'sandbox')

        # Payment method settings
        cash_enabled = 'cash_enabled' in request.form
        bank_transfer_enabled = 'bank_transfer_enabled' in request.form
        bank_account_details = request.form.get('bank_account_details', '').strip()

        # Build updated settings
        updated_settings = {
            "currency": "KES",
            "billing_cycle": "monthly",
            "water_rate_per_unit": water_rate,
            "billing": {
                "enable_water_billing": enable_water_billing,
                "enable_garbage_billing": enable_garbage_billing,
                "garbage_rate": garbage_rate,
                "late_payment_fee": late_payment_fee,
                "billing_day": billing_day,
                "enable_late_payment_fines": enable_late_payment_fines,
                "grace_period_days": grace_period_days,
                "fine_type": fine_type,
                "fine_rate": fine_rate,
                "fine_frequency": fine_frequency,
                "max_fine_amount": max_fine_amount
            },
            "payment_methods": {
                "mpesa": {
                    "enabled": mpesa_enabled,
                    "consumer_key": mpesa_consumer_key,
                    "consumer_secret": mpesa_consumer_secret,
                    "shortcode": mpesa_shortcode,
                    "passkey": mpesa_passkey,
                    "environment": mpesa_environment
                },
                "cash": {
                    "enabled": cash_enabled
                },
                "bank_transfer": {
                    "enabled": bank_transfer_enabled,
                    "account_details": bank_account_details
                }
            }
        }

        # Update property settings
        mongo.db.properties.update_one(
            {"_id": property_id_obj},
            {"$set": {
                "settings": updated_settings,
                "updated_at": datetime.now()
            }}
        )

        flash('Property billing settings updated successfully!', 'success')
        return redirect(url_for('property_settings', property_id=property_id))

    except ValueError as e:
        if "invalid literal" in str(e).lower():
            flash('Please enter valid numeric values for rates and fees.', 'danger')
        else:
            flash('Session expired. Please login again.', 'danger')
            return redirect(url_for('login'))
        return redirect(url_for('property_settings', property_id=property_id))
    except Exception as e:
        app.logger.error(f"Error updating property settings: {e}")
        flash('Error updating property settings.', 'danger')
        return redirect(url_for('property_settings', property_id=property_id))

@app.route('/s/<short_code>')
def redirect_short_url(short_code):
    """Handle short URL redirects."""
    try:
        # Find the short URL in database
        short_url_doc = mongo.db.short_urls.find_one({"short_code": short_code})

        if not short_url_doc:
            flash('Link not found or expired.', 'danger')
            return redirect(url_for('login'))

        # Increment click count
        mongo.db.short_urls.update_one(
            {"short_code": short_code},
            {"$inc": {"click_count": 1}, "$set": {"last_accessed": datetime.now()}}
        )

        # Redirect to the original URL
        return redirect(short_url_doc['long_url'])

    except Exception as e:
        app.logger.error(f"Error handling short URL redirect: {e}")
        flash('Invalid link.', 'danger')
        return redirect(url_for('login'))

@app.route('/smartwater')
def smartwater():
    return render_template('smartwater.html')

@app.route('/api/csrf-token')
@login_required
def api_csrf_token():
    """API endpoint to get fresh CSRF token"""
    try:
        from flask_wtf.csrf import generate_csrf
        token = generate_csrf()
        return jsonify({
            'csrf_token': token,
            'status': 'success'
        })
    except Exception as e:
        app.logger.error(f"Error generating CSRF token: {e}")
        return jsonify({
            'error': 'Failed to generate CSRF token',
            'status': 'error'
        }), 500

@app.route('/api/properties')
@login_required
def api_properties():
    """API endpoint to get properties for dropdown"""
    try:
        admin_id = get_admin_id()
        current_property_id = get_current_property_id()
        properties = get_user_properties()

        # Format properties for dropdown
        properties_data = []
        for prop in properties:
            properties_data.append({
                'id': str(prop['_id']),
                'name': prop['name'],
                'is_current': str(prop['_id']) == str(current_property_id)
            })

        return jsonify({
            'properties': properties_data,
            'current_property_id': str(current_property_id)
        })

    except Exception as e:
        app.logger.error(f"Error getting properties API: {e}")
        return jsonify({'error': 'Failed to load properties'}), 500

# Add to your initialization block
if mongo:
    with app.app_context():
        initialize_properties_collection()  # Add this line
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
    

def get_rate_per_unit(admin_id, property_id=None):
    """Get rate per unit for admin with property-specific override."""
    # First check if property-specific rate is available
    if property_id:
        try:
            property_rate = get_property_water_rate(property_id)
            if property_rate:
                return property_rate
        except Exception as e:
            app.logger.error(f"Error getting property rate: {e}")

    # Fall back to SMS config rate
    sms_config = mongo.db.sms_config.find_one({"admin_id": admin_id})
    if sms_config:
        return sms_config.get('rate_per_unit', RATE_PER_UNIT)

    # Finally fall back to admin default rate
    admin = mongo.db.admins.find_one({"_id": admin_id})
    return admin.get('rate_per_unit', RATE_PER_UNIT) if admin else RATE_PER_UNIT


def calculate_dashboard_analytics(admin_id):
    """Calculate analytics data for dashboard."""
    try:
        current_month = datetime.now().replace(day=1)

        # Monthly consumption for current month
        monthly_pipeline = [
            {
                "$match": {
                    "admin_id": admin_id,
                    "date_recorded": {"$gte": current_month}
                }
            },
            {
                "$group": {
                    "_id": None,
                    "total_usage": {"$sum": "$usage"},
                    "total_revenue": {"$sum": "$bill_amount"},
                    "count": {"$sum": 1}
                }
            }
        ]

        monthly_result = list(mongo.db.meter_readings.aggregate(monthly_pipeline))
        monthly_consumption = monthly_result[0]['total_usage'] if monthly_result else 0
        monthly_revenue = monthly_result[0]['total_revenue'] if monthly_result else 0
        reading_count = monthly_result[0]['count'] if monthly_result else 0

        # Total revenue (all time) - Combined water + rent
        # Water revenue
        water_revenue_pipeline = [
            {
                "$match": {"admin_id": admin_id}
            },
            {
                "$group": {
                    "_id": None,
                    "total_revenue": {"$sum": "$bill_amount"}
                }
            }
        ]

        water_revenue_result = list(mongo.db.meter_readings.aggregate(water_revenue_pipeline))
        water_revenue = water_revenue_result[0]['total_revenue'] if water_revenue_result else 0

        # Rent revenue
        rent_revenue_pipeline = [
            {
                "$match": {"admin_id": admin_id}
            },
            {
                "$group": {
                    "_id": None,
                    "total_revenue": {"$sum": "$amount_paid"}
                }
            }
        ]

        rent_revenue_result = list(mongo.db.payment_collections.aggregate(rent_revenue_pipeline))
        rent_revenue = rent_revenue_result[0]['total_revenue'] if rent_revenue_result else 0

        # Combined total revenue
        total_revenue = water_revenue + rent_revenue

        # Average usage per tenant
        tenant_count = mongo.db.tenants.count_documents({"admin_id": admin_id})
        avg_usage = monthly_consumption / tenant_count if tenant_count > 0 else 0

        # Monthly breakdown for charts (last 12 months)
        twelve_months_ago = datetime.now() - timedelta(days=365)

        # Monthly water revenue
        monthly_water_pipeline = [
            {
                "$match": {
                    "admin_id": admin_id,
                    "date_recorded": {"$gte": twelve_months_ago}
                }
            },
            {
                "$group": {
                    "_id": {
                        "year": {"$year": "$date_recorded"},
                        "month": {"$month": "$date_recorded"}
                    },
                    "revenue": {"$sum": "$bill_amount"},
                    "consumption": {"$sum": "$usage"}
                }
            },
            {"$sort": {"_id.year": 1, "_id.month": 1}}
        ]

        monthly_water_data = list(mongo.db.meter_readings.aggregate(monthly_water_pipeline))

        # Monthly rent revenue
        monthly_rent_pipeline = [
            {
                "$match": {
                    "admin_id": admin_id,
                    "collection_date": {"$gte": twelve_months_ago}
                }
            },
            {
                "$group": {
                    "_id": {
                        "year": {"$year": "$collection_date"},
                        "month": {"$month": "$collection_date"}
                    },
                    "revenue": {"$sum": "$amount_paid"}
                }
            },
            {"$sort": {"_id.year": 1, "_id.month": 1}}
        ]

        monthly_rent_data = list(mongo.db.payment_collections.aggregate(monthly_rent_pipeline))

        return {
            'monthly_consumption': round(monthly_consumption, 1),
            'total_revenue': round(total_revenue, 2),
            'avg_usage': round(avg_usage, 1),
            'monthly_water_data': monthly_water_data,
            'monthly_rent_data': monthly_rent_data,
            'water_revenue': round(water_revenue, 2),
            'rent_revenue': round(rent_revenue, 2)
        }

    except Exception as e:
        print(f"Error calculating analytics: {e}")
        return {
            'monthly_consumption': 0,
            'total_revenue': 0,
            'avg_usage': 0,
            'monthly_water_data': [],
            'monthly_rent_data': [],
            'water_revenue': 0,
            'rent_revenue': 0
        }



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

def calculate_late_payment_fine(bill, property_settings):
    """Calculate late payment fine for a bill based on property settings"""
    try:
        # Check if late payment fines are enabled
        billing_settings = property_settings.get('billing', {})
        if not billing_settings.get('enable_late_payment_fines', False):
            return 0.0

        # Get current date and bill due date
        current_date = datetime.now()
        due_date = bill.get('due_date')

        if not due_date:
            return 0.0

        # Convert due_date if it's a string
        if isinstance(due_date, str):
            try:
                due_date = datetime.strptime(due_date, '%Y-%m-%d')
            except ValueError:
                return 0.0

        # Calculate days overdue
        days_overdue = (current_date - due_date).days
        grace_period = billing_settings.get('grace_period_days', 7)

        # No fine if within grace period or bill is fully paid
        if days_overdue <= grace_period or bill.get('payment_status') == 'paid':
            return 0.0

        # Calculate outstanding amount
        bill_amount = float(bill.get('bill_amount', 0))
        amount_paid = float(bill.get('amount_paid', 0))
        outstanding_amount = bill_amount - amount_paid

        if outstanding_amount <= 0:
            return 0.0

        # Get fine configuration
        fine_type = billing_settings.get('fine_type', 'percentage')
        fine_rate = float(billing_settings.get('fine_rate', 5))
        fine_frequency = billing_settings.get('fine_frequency', 'one_time')
        max_fine_amount = float(billing_settings.get('max_fine_amount', 0))

        # Calculate base fine
        if fine_type == 'fixed':
            base_fine = fine_rate
        else:  # percentage
            base_fine = outstanding_amount * (fine_rate / 100)

        # Apply frequency multiplier for compound fines
        final_fine = base_fine
        if fine_frequency == 'monthly':
            # Calculate how many months overdue (after grace period)
            effective_overdue_days = max(0, days_overdue - grace_period)
            months_overdue = max(1, (effective_overdue_days // 30) + 1)
            final_fine = base_fine * months_overdue

        # Apply maximum fine limit if set
        if max_fine_amount > 0:
            final_fine = min(final_fine, max_fine_amount)

        return round(final_fine, 2)

    except Exception as e:
        app.logger.error(f"Error calculating fine for bill {bill.get('_id', 'unknown')}: {str(e)}")
        return 0.0

def get_property_billing_settings(property_id, admin_id):
    """Get billing settings for a property, with fallback to admin defaults"""
    try:
        if property_id:
            property_doc = mongo.db.properties.find_one({
                '_id': ObjectId(property_id),
                'admin_id': admin_id
            })
            if property_doc and 'billing_settings' in property_doc:
                return property_doc['billing_settings']

        # Fallback to admin defaults
        admin = mongo.db.admins.find_one({'_id': admin_id})
        if admin and 'default_billing_settings' in admin:
            return admin['default_billing_settings']

        # Default settings if none found
        return {
            'billing': {
                'enable_late_payment_fines': False,
                'grace_period_days': 7,
                'fine_type': 'percentage',
                'fine_rate': 5,
                'fine_frequency': 'one_time',
                'max_fine_amount': 1000
            }
        }

    except Exception as e:
        app.logger.error(f"Error getting property billing settings: {str(e)}")
        return {}


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

def build_tenant_search_query(admin_id, search_query="", search_type="all", property_id=None):
    """Build MongoDB query for tenant search with proper property isolation."""
    base_query = {"admin_id": admin_id}

    # CRITICAL: Add property_id filtering for data separation
    if property_id:
        base_query["property_id"] = property_id
    else:
        # Get current property context to ensure data isolation
        current_property_id = get_current_property_id()
        if current_property_id:
            base_query["property_id"] = current_property_id
    
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
    # CSP-compliant headers with external resources only, no unsafe-inline
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://pagead2.googlesyndication.com; img-src 'self' data: https://via.placeholder.com; style-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; font-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com data:;"
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
                if not till or not re.match(r'^\d{6,10}$', till):
                    flash('Till number must be between 6-10 digits', 'danger')
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

@app.route("/manage_tenants",methods=["GET","POST"])
@login_required
def manage_tenants():
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    if mongo is None:
        flash('Database connection error. Please try again later.', 'danger')
        return render_template('error.html', error_message="Database unavailable")

    # Get current property context for data isolation
    current_property_id = get_current_property_id()
    if not current_property_id:
        flash('No property selected. Please select a property first.', 'warning')
        return redirect(url_for('properties'))

    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', DEFAULT_PER_PAGE, type=int), 100)  # Limit max per_page
    search_query = request.args.get('search', '').strip()
    search_type = request.args.get('search_type', 'all')

    # Build query and get tenants with proper property isolation
    query = build_tenant_search_query(admin_id, search_query, search_type, current_property_id)
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
        # Get rate per unit for current property
    property_id = get_current_property_id()
    rate_per_unit = get_rate_per_unit(admin_id, property_id)
    # Add subscription info
    admin = mongo.db.admins.find_one({"_id": admin_id})
    tier = admin.get('subscription_tier', 'starter')
    tier_config = SUBSCRIPTION_TIERS.get(tier, SUBSCRIPTION_TIERS['starter'])
    return render_template(
        'tenants.html',
        tenants=tenants,
        pagination=pagination,
        subscription_tier_name=tier_config['name'],
        search_query=search_query,
        search_type=search_type,
        rate_per_unit=rate_per_unit,
        tenant_count=total_count,
        max_tenants=tier_config['max_tenants']
        )
    
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

    # Calculate analytics data
    analytics_data = calculate_dashboard_analytics(admin_id)

    return render_template(
        'dashboard.html',
        tenants=tenants,
        #pagination=pagination,
        readings=formatted_readings,
        current_billing_period=current_billing_period,
        readings_count=readings_count,  # Add this line
        #search_query=search_query,
        #search_type=search_type,
        rate_per_unit=rate_per_unit,
        tenant_count=total_count,
        max_tenants=tier_config['max_tenants'],
        subscription_tier_name=tier_config['name'],
        monthly_consumption=analytics_data['monthly_consumption'],
        total_revenue=analytics_data['total_revenue'],
        avg_usage=analytics_data['avg_usage'],
        properties=list(mongo.db.properties.find({'admin_id': admin_id})) or [{'name': 'Main Property'}],
        analytics_data=analytics_data
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
        current_property_id = get_current_property_id()

        if not current_property_id:
            return jsonify({"error": "No property selected"}), 400

        tenant_id_obj = ObjectId(tenant_id)

        # Validate tenant belongs to current property (CRITICAL for data separation)
        tenant = mongo.db.tenants.find_one({
            "_id": tenant_id_obj,
            "admin_id": admin_id,
            "property_id": current_property_id
        })

        if not tenant:
            return jsonify({"error": "Tenant not found or not in current property"}), 404

        house_number = tenant.get('house_number')

        # Get all readings for this tenant with property isolation
        # Sort by date_recorded descending (newest first)
        tenant_readings = list(mongo.db.meter_readings.find({
            "tenant_id": tenant_id_obj,
            "admin_id": admin_id,
            "property_id": current_property_id
        }).sort("date_recorded", -1))
        
        # If no tenant readings, check for house readings by house_number with property isolation
        if not tenant_readings and house_number:
            house_readings = list(mongo.db.meter_readings.find({
                "house_number": house_number,
                "admin_id": admin_id,
                "property_id": current_property_id
            }).sort("date_recorded", -1).limit(1))
            
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

@app.route('/api/maintenance_request', methods=['POST'])
def maintenance_request():
    """Handle maintenance request submissions from tenant portal"""
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = ['tenant_id', 'property_id', 'type', 'description', 'contact_phone']
        if not all(field in data for field in required_fields):
            return jsonify({'success': False, 'message': 'Missing required fields'}), 400

        # Get tenant and property information
        tenant_id = ObjectId(data['tenant_id'])
        property_id = ObjectId(data['property_id'])

        tenant = mongo.db.tenants.find_one({'_id': tenant_id})
        property_doc = mongo.db.properties.find_one({'_id': property_id})

        if not tenant or not property_doc:
            return jsonify({'success': False, 'message': 'Invalid tenant or property'}), 400

        # Create maintenance request document
        maintenance_request = {
            'tenant_id': tenant_id,
            'property_id': property_id,
            'admin_id': tenant['admin_id'],
            'tenant_name': tenant['name'],
            'tenant_phone': tenant['phone'],
            'house_number': tenant.get('house_number', ''),
            'property_name': property_doc['name'],
            'request_type': data['type'],
            'description': data['description'],
            'contact_phone': data['contact_phone'],
            'priority': data.get('priority', 'medium'),
            'status': 'pending',
            'created_at': datetime.now(),
            'updated_at': datetime.now(),
            'notes': []
        }

        # Insert maintenance request
        result = mongo.db.maintenance_requests.insert_one(maintenance_request)

        if result.inserted_id:
            # Send SMS notification to landlord if configured
            try:
                admin = mongo.db.admins.find_one({'_id': tenant['admin_id']})
                if admin and admin.get('phone'):
                    message = f"New maintenance request from {tenant['name']} at {property_doc['name']} - {data['type']}: {data['description'][:50]}{'...' if len(data['description']) > 50 else ''}"
                    send_message(admin['phone'], message)
            except Exception as e:
                app.logger.error(f"Failed to send maintenance request SMS: {str(e)}")

            return jsonify({
                'success': True,
                'message': 'Maintenance request submitted successfully',
                'request_id': str(result.inserted_id)
            })
        else:
            return jsonify({'success': False, 'message': 'Failed to submit request'}), 500

    except Exception as e:
        app.logger.error(f"Error in maintenance request: {str(e)}")
        return jsonify({'success': False, 'message': 'Server error occurred'}), 500

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
        property_id = get_current_property_id()

        new_tenant = {
            "_id": tenant_id,
            "name": name,
            "phone": formatted_phone,
            "house_number": house_number,
            "admin_id": admin_id,
            "property_id": property_id,
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

@app.route('/water' ,methods=['GET','POST'])
@login_required
def water_utility():
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
    return render_template('water.html',
        tenants=tenants,
        pagination=pagination,
        search_query=search_query,
        search_type=search_type)


@app.route('/garbage', methods=['GET','POST'])
@login_required
def garbage_utility():
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))

    if mongo is None:
        flash('Database connection error', 'danger')
        return redirect(url_for('db_error'))

    # Get current property
    try:
        current_property_id = session.get('current_property_id')
        if not current_property_id:
            # Get first property for this admin
            property_doc = mongo.db.properties.find_one({"admin_id": admin_id})
            if property_doc:
                current_property_id = property_doc['_id']
                session['current_property_id'] = str(current_property_id)
            else:
                flash('No properties found. Please create a property first.', 'warning')
                return redirect(url_for('properties'))
        else:
            current_property_id = ObjectId(current_property_id)
    except Exception as e:
        flash('Property selection error. Please try again.', 'danger')
        return redirect(url_for('properties'))

    # Get property settings
    property_settings = mongo.db.properties.find_one({"_id": current_property_id})
    if not property_settings:
        flash('Property not found.', 'danger')
        return redirect(url_for('properties'))

    # Check if garbage billing is enabled
    garbage_billing_enabled = property_settings.get('billing', {}).get('enable_garbage_billing', False)
    garbage_rate = property_settings.get('billing', {}).get('garbage_rate', 0)

    if not garbage_billing_enabled:
        flash('Garbage billing is not enabled for this property. Please enable it in property settings.', 'warning')
        return redirect(url_for('update_property_settings', property_id=current_property_id))

    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', DEFAULT_PER_PAGE, type=int), 100)
    search_query = request.args.get('search', '').strip()
    month_filter = request.args.get('month', '')

    # Get all tenants for this property
    tenants = list(mongo.db.tenants.find({"admin_id": admin_id, "property_id": current_property_id}))

    # Build query for garbage bills
    bills_query = {"admin_id": admin_id, "property_id": current_property_id, "bill_type": "garbage"}

    if search_query:
        escaped_query = re.escape(search_query)
        bills_query["tenant_name"] = {"$regex": escaped_query, "$options": "i"}

    if month_filter:
        try:
            filter_date = datetime.strptime(month_filter, '%Y-%m')
            start_date = filter_date.replace(day=1)
            if filter_date.month == 12:
                end_date = filter_date.replace(year=filter_date.year + 1, month=1, day=1)
            else:
                end_date = filter_date.replace(month=filter_date.month + 1, day=1)
            bills_query["bill_month"] = {"$gte": start_date, "$lt": end_date}
        except ValueError:
            pass

    # Get garbage bills with pagination
    bills_pipeline = [
        {"$match": bills_query},
        {"$facet": {
            "bills": [
                {"$sort": {"bill_month": -1, "tenant_name": 1}},
                {"$skip": (page - 1) * per_page},
                {"$limit": per_page}
            ],
            "total": [{"$count": "count"}]
        }}
    ]

    bills_result = list(mongo.db.bills.aggregate(bills_pipeline))
    garbage_bills = bills_result[0]['bills'] if bills_result else []
    total_bills = bills_result[0]['total'][0]['count'] if bills_result and bills_result[0]['total'] else 0

    # Create pagination object
    total_pages = (total_bills + per_page - 1) // per_page
    pagination = {
        'page': page,
        'per_page': per_page,
        'total': total_bills,
        'pages': total_pages,
        'has_prev': page > 1,
        'has_next': page < total_pages
    }

    # Get collection schedule
    collection_schedule = mongo.db.garbage_schedules.find_one({"admin_id": admin_id, "property_id": current_property_id})

    # Calculate statistics
    pending_bills_count = mongo.db.bills.count_documents({
        "admin_id": admin_id,
        "property_id": current_property_id,
        "bill_type": "garbage",
        "status": {"$in": ["pending", "overdue"]}
    })

    # Calculate monthly revenue
    current_month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = current_month_start.replace(month=current_month_start.month + 1) if current_month_start.month < 12 else current_month_start.replace(year=current_month_start.year + 1, month=1)

    monthly_revenue_pipeline = [
        {"$match": {
            "admin_id": admin_id,
            "property_id": current_property_id,
            "bill_type": "garbage",
            "status": "paid",
            "payment_date": {"$gte": current_month_start, "$lt": next_month}
        }},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]
    monthly_revenue_result = list(mongo.db.bills.aggregate(monthly_revenue_pipeline))
    monthly_revenue = monthly_revenue_result[0]['total'] if monthly_revenue_result else 0

    # Generate months for filter
    months = []
    current_date = datetime.now()
    for i in range(12):
        month_date = current_date.replace(month=current_date.month - i) if current_date.month > i else current_date.replace(year=current_date.year - 1, month=12 - (i - current_date.month))
        months.append({
            'name': month_date.strftime('%B %Y'),
            'value': month_date.strftime('%Y-%m')
        })

    return render_template('garbage.html',
                         tenants=tenants,
                         garbage_bills=garbage_bills,
                         pagination=pagination,
                         search_query=search_query,
                         collection_schedule=collection_schedule,
                         pending_bills_count=pending_bills_count,
                         monthly_revenue=monthly_revenue,
                         garbage_rate=garbage_rate,
                         months=months,
                         current_month=month_filter,
                         datetime=datetime)


@app.route('/generate_garbage_bills', methods=['POST'])
@login_required
def generate_garbage_bills():
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))

    if mongo is None:
        flash('Database connection error', 'danger')
        return redirect(url_for('db_error'))

    current_property_id = ObjectId(session.get('current_property_id'))

    # Get form data
    bill_month_str = request.form.get('bill_month')
    due_date_str = request.form.get('due_date')

    try:
        bill_month = datetime.strptime(bill_month_str, '%Y-%m')
        due_date = datetime.strptime(due_date_str, '%Y-%m-%d')
    except ValueError:
        flash('Invalid date format.', 'danger')
        return redirect(url_for('garbage_utility'))

    # Get property settings
    property_settings = mongo.db.properties.find_one({"_id": current_property_id})
    if not property_settings:
        flash('Property not found.', 'danger')
        return redirect(url_for('garbage_utility'))

    garbage_rate = property_settings.get('billing', {}).get('garbage_rate', 0)
    if garbage_rate <= 0:
        flash('Please set a valid garbage rate in property settings first.', 'warning')
        return redirect(url_for('garbage_utility'))

    # Get all tenants for this property
    tenants = list(mongo.db.tenants.find({"admin_id": admin_id, "property_id": current_property_id}))

    if not tenants:
        flash('No tenants found for this property.', 'warning')
        return redirect(url_for('garbage_utility'))

    # Check if bills already exist for this month
    existing_bills = mongo.db.bills.count_documents({
        "admin_id": admin_id,
        "property_id": current_property_id,
        "bill_type": "garbage",
        "bill_month": bill_month
    })

    if existing_bills > 0:
        flash(f'Garbage bills for {bill_month.strftime("%B %Y")} already exist.', 'warning')
        return redirect(url_for('garbage_utility'))

    # Generate bills for all tenants
    bills_to_insert = []
    for tenant in tenants:
        bill = {
            "_id": ObjectId(),
            "admin_id": admin_id,
            "property_id": current_property_id,
            "tenant_id": tenant['_id'],
            "tenant_name": tenant['name'],
            "house_number": tenant['house_number'],
            "bill_type": "garbage",
            "bill_month": bill_month,
            "amount": garbage_rate,
            "outstanding_amount": garbage_rate,
            "due_date": due_date,
            "status": "pending",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        }
        bills_to_insert.append(bill)

    try:
        result = mongo.db.bills.insert_many(bills_to_insert)
        flash(f'Successfully generated {len(result.inserted_ids)} garbage bills for {bill_month.strftime("%B %Y")}.', 'success')
    except Exception as e:
        flash('Error generating bills. Please try again.', 'danger')

    return redirect(url_for('garbage_utility'))


@app.route('/record_garbage_payment', methods=['POST'])
@login_required
def record_garbage_payment():
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))

    if mongo is None:
        flash('Database connection error', 'danger')
        return redirect(url_for('db_error'))

    # Get form data
    bill_id = request.form.get('bill_id')
    amount = float(request.form.get('amount', 0))
    payment_method = request.form.get('payment_method')
    reference = request.form.get('reference', '')
    payment_date_str = request.form.get('payment_date')

    try:
        payment_date = datetime.strptime(payment_date_str, '%Y-%m-%d')
    except ValueError:
        flash('Invalid payment date.', 'danger')
        return redirect(url_for('garbage_utility'))

    if not bill_id or amount <= 0 or not payment_method:
        flash('Please fill in all required fields.', 'danger')
        return redirect(url_for('garbage_utility'))

    # Get the bill
    bill = mongo.db.bills.find_one({"_id": ObjectId(bill_id), "admin_id": admin_id})
    if not bill:
        flash('Bill not found.', 'danger')
        return redirect(url_for('garbage_utility'))

    if bill['status'] == 'paid':
        flash('This bill has already been paid.', 'warning')
        return redirect(url_for('garbage_utility'))

    # Record the payment
    payment_record = {
        "_id": ObjectId(),
        "admin_id": admin_id,
        "property_id": bill['property_id'],
        "tenant_id": bill['tenant_id'],
        "bill_id": bill['_id'],
        "bill_type": "garbage",
        "amount": amount,
        "payment_method": payment_method,
        "reference": reference,
        "payment_date": payment_date,
        "recorded_at": datetime.now(timezone.utc),
        "recorded_by": admin_id
    }

    try:
        # Insert payment record
        mongo.db.payments.insert_one(payment_record)

        # Update bill status
        update_data = {
            "status": "paid",
            "payment_date": payment_date,
            "updated_at": datetime.now(timezone.utc)
        }

        if amount >= bill['outstanding_amount']:
            update_data["outstanding_amount"] = 0
        else:
            update_data["outstanding_amount"] = bill['outstanding_amount'] - amount

        mongo.db.bills.update_one(
            {"_id": ObjectId(bill_id)},
            {"$set": update_data}
        )

        flash(f'Payment of KES {amount:.2f} recorded successfully for {bill["tenant_name"]}.', 'success')

    except Exception as e:
        flash('Error recording payment. Please try again.', 'danger')

    return redirect(url_for('garbage_utility'))


@app.route('/update_collection_schedule', methods=['POST'])
@login_required
def update_collection_schedule():
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))

    if mongo is None:
        flash('Database connection error', 'danger')
        return redirect(url_for('db_error'))

    current_property_id = ObjectId(session.get('current_property_id'))

    # Get form data
    collection_days = request.form.getlist('collection_days')
    collection_time = request.form.get('collection_time')
    notes = request.form.get('notes', '')

    if not collection_days:
        flash('Please select at least one collection day.', 'warning')
        return redirect(url_for('garbage_utility'))

    # Calculate next collection date
    next_collection = None
    today = datetime.now().date()
    for i in range(7):  # Look ahead 7 days
        check_date = today + timedelta(days=i)
        if check_date.strftime('%A') in collection_days:
            next_collection = check_date
            break

    schedule_data = {
        "admin_id": admin_id,
        "property_id": current_property_id,
        "collection_days": collection_days,
        "collection_time": collection_time,
        "notes": notes,
        "next_collection": next_collection,
        "updated_at": datetime.now(timezone.utc)
    }

    try:
        # Upsert collection schedule
        mongo.db.garbage_schedules.update_one(
            {"admin_id": admin_id, "property_id": current_property_id},
            {"$set": schedule_data},
            upsert=True
        )
        flash('Collection schedule updated successfully.', 'success')
    except Exception as e:
        flash('Error updating collection schedule. Please try again.', 'danger')

    return redirect(url_for('garbage_utility'))


@app.route('/update_garbage_bill_statuses', methods=['POST'])
@login_required
def update_garbage_bill_statuses():
    """Update overdue status for garbage bills"""
    try:
        admin_id = get_admin_id()
    except ValueError:
        return jsonify({'error': 'Session expired'}), 401

    if mongo is None:
        return jsonify({'error': 'Database connection error'}), 500

    current_property_id = ObjectId(session.get('current_property_id'))
    today = datetime.now().date()

    # Update bills that are past due date to overdue status
    result = mongo.db.bills.update_many(
        {
            "admin_id": admin_id,
            "property_id": current_property_id,
            "bill_type": "garbage",
            "status": "pending",
            "due_date": {"$lt": today}
        },
        {
            "$set": {
                "status": "overdue",
                "updated_at": datetime.now(timezone.utc)
            }
        }
    )

    return jsonify({
        'success': True,
        'updated_count': result.modified_count
    })


@app.route('/houses', methods=['GET'])
@login_required
def houses():
    """Display houses management page with all houses"""
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    # Get current property context for data isolation
    current_property_id = get_current_property_id()
    if not current_property_id:
        flash('No property selected. Please select a property first.', 'warning')
        return redirect(url_for('properties'))

    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', DEFAULT_PER_PAGE, type=int), 100)  # Limit max per_page
    search_query = request.args.get('search', '').strip()
    status = request.args.get('status', '').strip()

    # Build query for houses with proper property isolation
    query = {"admin_id": admin_id, "property_id": current_property_id}
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

def get_last_house_reading(house_number, admin_id, property_id=None):
    """Get the last reading for a specific house number from meter_readings with property isolation."""
    if not house_number:
        return None

    query = {"house_number": house_number, "admin_id": admin_id}

    # Add property filtering for data separation
    if property_id:
        query["property_id"] = property_id
    else:
        # Get current property context to ensure data isolation
        current_property_id = get_current_property_id()
        if current_property_id:
            query["property_id"] = current_property_id

    last_reading = mongo.db.meter_readings.find_one(
        query,
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
        # Get current property context for data isolation
        current_property_id = get_current_property_id()
        if not current_property_id:
            flash('No property selected. Please select a property first.', 'warning')
            return redirect(url_for('properties'))

        tenant_id_obj = ObjectId(tenant_id)

        # Validate tenant belongs to current property (CRITICAL for data separation)
        tenant = mongo.db.tenants.find_one({
            "_id": tenant_id_obj,
            "admin_id": admin_id,
            "property_id": current_property_id
        })

        if not tenant:
            flash('Tenant not found or not in current property', 'danger')
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

        # Calculate billing using property-specific rate
        usage = current_reading - previous_reading
        property_id = get_current_property_id()
        rate_per_unit = get_rate_per_unit(admin_id, property_id)
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

            # Calculate late payment fine for current bill
            current_bill = {
                'bill_amount': bill_amount,
                'amount_paid': 0,
                'due_date': datetime.now() + timedelta(days=30),  # Assuming 30 day payment period
                'payment_status': 'unpaid',
                'property_id': tenant.get('property_id')  # Get property from tenant if available
            }
            property_settings = get_property_billing_settings(current_bill.get('property_id'), admin_id)
            late_fine = calculate_late_payment_fine(current_bill, property_settings)

            # Generate secure access token for tenant portal
            access_token = generate_tenant_access_token(tenant_id_obj, admin_id, expires_in_hours=24)
            long_portal_link = f"{request.url_root}tenant_portal/{access_token}"
            portal_link = shorten_url(long_portal_link, f"tenant_{tenant_id_obj}_{datetime.now().strftime('%Y%m')}")

            # Construct message with fine information
            if total_arrears > 1:
                if late_fine > 0:
                    message = (
                        f"Water Bill Alert: {tenant['name']}, House {house_number}. "
                        f"Current bill: KES {bill_amount:.2f}. "
                        f"Outstanding arrears: KES {total_arrears:.2f}. "
                        f"Late payment fine: KES {late_fine:.2f}. "
                        f"Total due: KES {bill_amount + total_arrears + late_fine:.2f}. "
                        f"{payment_info} "
                        f"View details: {portal_link} "
                        f"From {admin_name} - {admin_phone}"
                    )
                else:
                    message = (
                        f"Water Bill Alert: {tenant['name']}, House {house_number}. "
                        f"Current bill: KES {bill_amount:.2f}. "
                        f"Outstanding arrears: KES {total_arrears:.2f}. "
                        f"{payment_info} "
                        f"View your usage history: {portal_link} "
                        f"From {admin_name} - {admin_phone}"
                    )
            else:
                if late_fine > 0:
                    message = (
                        f"Water Bill Alert: {tenant['name']}, House {house_number}. "
                        f"Current bill: KES {bill_amount:.2f}. "
                        f"Late payment fine: KES {late_fine:.2f}. "
                        f"Total due: KES {bill_amount + late_fine:.2f}. "
                        f"{payment_info} "
                        f"View details: {portal_link} "
                        f"From {admin_name} - {admin_phone}"
                    )
                else:
                    message = (
                        f"Water Bill Alert: {tenant['name']}, House {house_number}. "
                        f"Current bill: KES {bill_amount:.2f}. "
                        f"{payment_info} "
                        f"View your usage history: {portal_link} "
                        f"From {admin_name} - {admin_phone}"
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

        # Calculate billing using property-specific rate
        usage = current_reading - previous_reading
        # Get property_id from tenant record
        property_id = tenant.get('property_id')
        rate_per_unit = get_rate_per_unit(admin_id, property_id)
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
            # Generate secure access token for tenant portal
            access_token = generate_tenant_access_token(tenant_id_obj, admin_id, expires_in_hours=24)
            long_portal_link = f"{request.url_root}tenant_portal/{access_token}"
            portal_link = shorten_url(long_portal_link, f"tenant_{tenant_id_obj}_{datetime.now().strftime('%Y%m')}")

            if total_arrears > 1:
                message = (
                    f"Water Bill Alert: {tenant['name']}, House {house_number}. "
                    f"Current bill: KES {bill_amount:.2f}. "
                    f"Outstanding arrears: KES {total_arrears:.2f}. "
                    f"Total amount due: KES {bill_amount + total_arrears:.2f}. "
                    f"{payment_info} View history: {portal_link} From {admin_name} - {admin_phone}"
                )
            else:
                message = (
                    f"Water Bill Alert: {tenant['name']}, House {house_number}. "
                    f"Current bill: KES {bill_amount:.2f}. "
                    f"{payment_info} View history: {portal_link} From {admin_name} - {admin_phone}"
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

                # Generate secure access token for tenant portal
                access_token = generate_tenant_access_token(tenant_id_obj, admin_id, expires_in_hours=24)
                long_portal_link = f"{request.url_root}tenant_portal/{access_token}"
                portal_link = shorten_url(long_portal_link, f"tenant_{tenant_id_obj}_{datetime.now().strftime('%Y%m')}")

                # Create SMS message with arrears info
            if total_arrears > 1:  # Use smaller threshold for precision
                message = (
                    f"Rent Bill Alert: {tenant['name']}, House {house_number}. "
                    f"Current bill: KES {rent_amount:.2f}. "
                    f"Outstanding arrears: KES {total_arrears:.2f}. "
                    f"Total amount due: KES {rent_amount + total_arrears:.2f}. "
                    f"{payment_text} View history: {portal_link} From {admin_name} - {admin_phone}"
                )
            else:
                    message = (
                        f"Rent Bill Alert: {tenant['name']}, House {house_number}. "
                        f"Current bill: KES {rent_amount:.2f}. "
                        f"{payment_text} View history: {portal_link} From {admin_name} - {admin_phone}"
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

            # Calculate late payment fine
            property_settings = get_property_billing_settings(bill.get('property_id'), admin_id)
            fine_amount = calculate_late_payment_fine(bill, property_settings)
            bill['late_payment_fine'] = fine_amount
            bill['total_amount_due'] = bill['outstanding_amount'] + fine_amount

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

@app.route('/unallocated_payments', methods=['GET'])
@login_required
def unallocated_payments():
    """Display dashboard for unallocated M-Pesa payments requiring manual allocation"""
    try:
        # Get admin_id from session
        admin_id = get_admin_id()

        # Get pagination parameters
        page = request.args.get('page', 1, type=int)
        per_page = 10

        # Build query for unallocated payments
        query = {
            'admin_id': admin_id,
            'allocation_status': {'$in': ['pending', 'partial']}
        }

        # Get total count for pagination
        total_count = mongo.db.unallocated_payments.count_documents(query)

        # Get paginated unallocated payments
        unallocated_payments_list = list(mongo.db.unallocated_payments.find(query)
                                       .sort('payment_date', -1)
                                       .skip((page - 1) * per_page)
                                       .limit(per_page))

        # Enrich each payment with outstanding bills for that house
        enriched_payments = []
        for payment in unallocated_payments_list:
            # Get outstanding bills for this house (both water and rent)
            outstanding_bills = list(mongo.db.payments.find({
                'admin_id': admin_id,
                'house_id': payment['house_id'],
                'payment_status': {'$in': ['unpaid', 'partial']}
            }).sort('due_date', 1))

            # Add outstanding amounts to bills
            for bill in outstanding_bills:
                bill['outstanding_amount'] = bill['bill_amount'] - bill.get('amount_paid', 0)

            payment['outstanding_bills'] = outstanding_bills
            payment['total_outstanding'] = sum(bill['outstanding_amount'] for bill in outstanding_bills)
            enriched_payments.append(payment)

        # Calculate pagination info
        has_prev = page > 1
        has_next = (page * per_page) < total_count
        prev_num = page - 1 if has_prev else None
        next_num = page + 1 if has_next else None

        pagination = {
            'page': page,
            'per_page': per_page,
            'total': total_count,
            'has_prev': has_prev,
            'has_next': has_next,
            'prev_num': prev_num,
            'next_num': next_num,
            'pages': (total_count + per_page - 1) // per_page
        }

        # Calculate summary statistics
        total_unallocated = sum(payment['amount_remaining'] for payment in enriched_payments)
        pending_count = len([p for p in enriched_payments if p['allocation_status'] == 'pending'])
        partial_count = len([p for p in enriched_payments if p['allocation_status'] == 'partial'])

        return render_template('unallocated_payments.html',
                             payments=enriched_payments,
                             pagination=pagination,
                             total_unallocated=total_unallocated,
                             pending_count=pending_count,
                             partial_count=partial_count,
                             now=datetime.now().timestamp())

    except KeyError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    except Exception as e:
        app.logger.error(f"Error in unallocated payments dashboard: {str(e)}")
        flash(f'Error loading unallocated payments: {str(e)}', 'danger')
        return redirect(url_for('dashboard'))

@app.route('/allocate_payment/<unallocated_payment_id>', methods=['POST'])
@login_required
def allocate_payment(unallocated_payment_id):
    """Allocate unallocated payment to specific bills"""
    try:
        admin_id = get_admin_id()

        # Get the unallocated payment
        unallocated_payment = mongo.db.unallocated_payments.find_one({
            '_id': ObjectId(unallocated_payment_id),
            'admin_id': admin_id
        })

        if not unallocated_payment:
            flash('Unallocated payment not found', 'danger')
            return redirect(url_for('unallocated_payments'))

        # Get allocation data from form
        allocations = []
        total_allocated = 0

        # Parse form data for allocations (bill_id: amount)
        for key, value in request.form.items():
            if key.startswith('allocation_') and float(value or 0) > 0:
                bill_id = key.replace('allocation_', '')
                amount = float(value)
                allocations.append({
                    'bill_id': ObjectId(bill_id),
                    'amount': amount,
                    'allocated_at': datetime.now()
                })
                total_allocated += amount

        # Validate total allocation doesn't exceed remaining amount
        if total_allocated > unallocated_payment['amount_remaining']:
            flash(f'Total allocation (KES {total_allocated}) exceeds remaining amount (KES {unallocated_payment["amount_remaining"]})', 'danger')
            return redirect(url_for('unallocated_payments'))

        # Process each allocation
        successful_allocations = []
        for allocation in allocations:
            # Get the bill being paid
            bill = mongo.db.payments.find_one({
                '_id': allocation['bill_id'],
                'admin_id': admin_id
            })

            if bill:
                # Calculate new payment amount for this bill
                current_paid = bill.get('amount_paid', 0)
                new_paid = current_paid + allocation['amount']
                bill_amount = bill['bill_amount']

                # Determine new payment status
                if new_paid >= bill_amount:
                    new_status = 'paid'
                elif new_paid > 0:
                    new_status = 'partial'
                else:
                    new_status = 'unpaid'

                # Update the bill
                mongo.db.payments.update_one(
                    {'_id': allocation['bill_id']},
                    {
                        '$set': {
                            'amount_paid': new_paid,
                            'payment_status': new_status,
                            'last_payment_method': 'mpesa_auto_allocated',
                            'updated_at': datetime.now()
                        },
                        '$push': {
                            'payment_history': {
                                'amount': allocation['amount'],
                                'method': 'mpesa_auto_allocated',
                                'date': datetime.now(),
                                'mpesa_trans_id': unallocated_payment['mpesa_trans_id'],
                                'unallocated_payment_id': ObjectId(unallocated_payment_id),
                                'notes': f'Allocated from M-Pesa payment {unallocated_payment["mpesa_trans_id"]}'
                            }
                        }
                    }
                )

                successful_allocations.append({
                    'bill_id': allocation['bill_id'],
                    'bill_type': bill.get('bill_type', 'unknown'),
                    'month_year': bill.get('month_year', 'unknown'),
                    'amount': allocation['amount'],
                    'allocated_at': allocation['allocated_at']
                })

        # Update unallocated payment record
        new_amount_allocated = unallocated_payment['amount_allocated'] + total_allocated
        new_amount_remaining = unallocated_payment['amount_remaining'] - total_allocated

        # Determine new allocation status
        if new_amount_remaining <= 0:
            new_allocation_status = 'complete'
        elif new_amount_allocated > 0:
            new_allocation_status = 'partial'
        else:
            new_allocation_status = 'pending'

        mongo.db.unallocated_payments.update_one(
            {'_id': ObjectId(unallocated_payment_id)},
            {
                '$set': {
                    'amount_allocated': new_amount_allocated,
                    'amount_remaining': new_amount_remaining,
                    'allocation_status': new_allocation_status,
                    'updated_at': datetime.now()
                },
                '$push': {
                    'allocations': {
                        '$each': successful_allocations
                    }
                }
            }
        )

        # Send confirmation SMS to tenant
        if unallocated_payment.get('tenant_id'):
            tenant = mongo.db.tenants.find_one({'_id': unallocated_payment['tenant_id']})
            if tenant and tenant.get('phone'):
                try:
                    allocation_details = ', '.join([f"{alloc['bill_type']} KES {alloc['amount']}" for alloc in successful_allocations])
                    message = f"Payment allocated: {allocation_details}. House {unallocated_payment['house_number']}. Thank you!"
                    send_message(tenant['phone'], message)
                except Exception as sms_error:
                    app.logger.error(f"SMS error: {sms_error}")

        flash(f'Successfully allocated KES {total_allocated} to {len(successful_allocations)} bill(s)', 'success')
        return redirect(url_for('unallocated_payments'))

    except Exception as e:
        app.logger.error(f"Error allocating payment: {str(e)}")
        flash(f'Error allocating payment: {str(e)}', 'danger')
        return redirect(url_for('unallocated_payments'))

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

@app.route('/maintenance_requests')
@login_required
def maintenance_requests():
    """View and manage maintenance requests for all properties"""
    try:
        admin_id = get_admin_id()

        # Get filter parameters
        status_filter = request.args.get('status', 'all')
        property_filter = request.args.get('property', 'all')
        priority_filter = request.args.get('priority', 'all')

        # Build query
        query = {'admin_id': admin_id}

        if status_filter != 'all':
            query['status'] = status_filter

        if property_filter != 'all':
            query['property_id'] = ObjectId(property_filter)

        if priority_filter != 'all':
            query['priority'] = priority_filter

        # Get maintenance requests with pagination
        page = request.args.get('page', 1, type=int)
        per_page = 10

        total_requests = mongo.db.maintenance_requests.count_documents(query)
        requests_list = list(mongo.db.maintenance_requests.find(query)
                           .sort('created_at', -1)
                           .skip((page - 1) * per_page)
                           .limit(per_page))

        # Get properties for filter dropdown
        properties = list(mongo.db.properties.find({'admin_id': admin_id}))

        # Calculate summary stats
        stats = {
            'total': mongo.db.maintenance_requests.count_documents({'admin_id': admin_id}),
            'pending': mongo.db.maintenance_requests.count_documents({'admin_id': admin_id, 'status': 'pending'}),
            'in_progress': mongo.db.maintenance_requests.count_documents({'admin_id': admin_id, 'status': 'in_progress'}),
            'completed': mongo.db.maintenance_requests.count_documents({'admin_id': admin_id, 'status': 'completed'}),
        }

        # Pagination info
        pagination = {
            'page': page,
            'per_page': per_page,
            'total': total_requests,
            'has_prev': page > 1,
            'has_next': (page * per_page) < total_requests,
            'prev_num': page - 1 if page > 1 else None,
            'next_num': page + 1 if (page * per_page) < total_requests else None,
            'pages': (total_requests + per_page - 1) // per_page
        }

        return render_template('maintenance_requests.html',
                             requests=requests_list,
                             properties=properties,
                             stats=stats,
                             pagination=pagination,
                             current_filters={
                                 'status': status_filter,
                                 'property': property_filter,
                                 'priority': priority_filter
                             })

    except Exception as e:
        app.logger.error(f"Error loading maintenance requests: {str(e)}")
        flash('Error loading maintenance requests', 'danger')
        return redirect(url_for('dashboard'))

@app.route('/update_maintenance_request/<request_id>', methods=['POST'])
@login_required
def update_maintenance_request(request_id):
    """Update maintenance request status and add notes"""
    try:
        admin_id = get_admin_id()

        # Get the maintenance request
        maintenance_req = mongo.db.maintenance_requests.find_one({
            '_id': ObjectId(request_id),
            'admin_id': admin_id
        })

        if not maintenance_req:
            flash('Maintenance request not found', 'danger')
            return redirect(url_for('maintenance_requests'))

        # Get form data
        new_status = request.form.get('status')
        note = request.form.get('note', '').strip()

        # Prepare update
        update_data = {
            'updated_at': datetime.now()
        }

        if new_status and new_status != maintenance_req['status']:
            update_data['status'] = new_status

        # Add note if provided
        if note:
            update_data['$push'] = {
                'notes': {
                    'text': note,
                    'added_at': datetime.now(),
                    'added_by': 'admin'
                }
            }

        # Update the request
        result = mongo.db.maintenance_requests.update_one(
            {'_id': ObjectId(request_id)},
            {'$set': update_data} if '$push' not in update_data else {
                '$set': {k: v for k, v in update_data.items() if k != '$push'},
                '$push': update_data['$push']
            }
        )

        if result.modified_count > 0:
            # Send SMS notification to tenant if status changed
            if new_status and new_status != maintenance_req['status']:
                try:
                    tenant = mongo.db.tenants.find_one({'_id': maintenance_req['tenant_id']})
                    if tenant and tenant.get('phone'):
                        status_text = {
                            'pending': 'received and is pending review',
                            'in_progress': 'being worked on',
                            'completed': 'completed'
                        }.get(new_status, new_status)

                        message = f"Your maintenance request for {maintenance_req['request_type']} is now {status_text}."
                        if note:
                            message += f" Note: {note}"

                        send_message(tenant['phone'], message)
                except Exception as e:
                    app.logger.error(f"Failed to send maintenance update SMS: {str(e)}")

            flash('Maintenance request updated successfully', 'success')
        else:
            flash('No changes made to the request', 'info')

    except Exception as e:
        app.logger.error(f"Error updating maintenance request: {str(e)}")
        flash('Error updating maintenance request', 'danger')

    return redirect(url_for('maintenance_requests'))


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
        headers = ['Date', 'Previous Reading (m³)', 'Current Reading (m³)', 'Usage (m³)', 'Bill Amount', 'Payment Status', 'Amount Paid', 'Outstanding']
        start_row = 6
        
        worksheet.write('A6', 'Reading History', header_format)
        for col, header in enumerate(headers):
            worksheet.write(start_row + 1, col, header, header_format)
        
        # Write reading data with payment information
        for row, reading in enumerate(readings, start_row + 2):
            # Get payment information for this reading
            reading_month = reading['date_recorded'].strftime('%Y-%m')
            payment = mongo.db.payments.find_one({
                'tenant_id': tenant_id_obj,
                'admin_id': admin_id,
                'month_year': reading_month
            })

            # Determine payment status
            if payment:
                amount_paid = payment.get('amount_paid', 0)
                bill_amount = reading['bill_amount']
                outstanding = max(0, bill_amount - amount_paid)

                if outstanding == 0:
                    payment_status = 'Paid'
                elif amount_paid > 0:
                    payment_status = 'Partial'
                else:
                    payment_status = 'Unpaid'
            else:
                payment_status = 'Unpaid'
                amount_paid = 0
                outstanding = reading['bill_amount']

            worksheet.write(row, 0, reading['date_recorded'], date_format)
            worksheet.write(row, 1, reading['previous_reading'], cell_format)
            worksheet.write(row, 2, reading['current_reading'], cell_format)
            worksheet.write(row, 3, reading['usage'], cell_format)
            worksheet.write(row, 4, reading['bill_amount'], currency_format)
            worksheet.write(row, 5, payment_status, cell_format)
            worksheet.write(row, 6, amount_paid, currency_format)
            worksheet.write(row, 7, outstanding, currency_format)
        
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
        use_house_number_as_account = 'use_house_number_as_account' in request.form

        # Security check: Only PayBill users can enable house number as account feature
        if use_house_number_as_account and admin.get('payment_method') != 'paybill':
            flash('House number as account reference is only available for PayBill users', 'danger')
            return redirect(url_for('sms_config'))

        if rate_per_unit is None:
            flash('Rate per unit is required', 'danger')
            return redirect(url_for('sms_config'))

        try:
            config_data = {
                "rate_per_unit": rate_per_unit,
                "use_house_number_as_account": use_house_number_as_account,
                "updated_at": datetime.now()
            }

            if config:
                mongo.db.sms_config.update_one(
                    {"_id": config["_id"]},
                    {"$set": config_data}
                )
            else:
                config_data["admin_id"] = admin_id
                mongo.db.sms_config.insert_one(config_data)

            flash('Configuration updated successfully!', 'success')
        except Exception as e:
            flash(f'Error updating configuration: {str(e)}', 'danger')

    # Get M-Pesa configuration
    mpesa_config = mongo.db.mpesa_config.find_one({"admin_id": admin_id})
    if mpesa_config:
        # Decrypt sensitive fields for display (but not the actual secrets)
        mpesa_config = decrypt_mpesa_credentials(mpesa_config)
        # For security, don't show the full secrets in the form
        if mpesa_config.get('consumer_secret'):
            mpesa_config['consumer_secret'] = '*' * 20
        if mpesa_config.get('passkey'):
            mpesa_config['passkey'] = '*' * 20

    return render_template('sms_config.html',
                          config=config,
                          admin=admin,
                          mpesa_config=mpesa_config,
                          rate_per_unit=RATE_PER_UNIT,
                          talksasa_api_key=TALKSASA_API_KEY,
                          talksasa_sender_id=TALKSASA_SENDER_ID)

@app.route('/save_mpesa_config', methods=['POST'])
@login_required
def save_mpesa_config():
    """Save M-Pesa API configuration with encryption"""
    try:
        admin_id = get_admin_id()

        # Get form data
        consumer_key = request.form.get('consumer_key', '').strip()
        consumer_secret = request.form.get('consumer_secret', '').strip()
        shortcode = request.form.get('shortcode', '').strip()
        passkey = request.form.get('passkey', '').strip()
        environment = request.form.get('environment', 'sandbox')

        # Validate required fields
        if not all([consumer_key, shortcode, environment]):
            flash('Consumer Key and Shortcode are required', 'danger')
            return redirect(url_for('sms_config'))

        # Don't update secrets if they're masked (showing asterisks)
        existing_config = mongo.db.mpesa_config.find_one({"admin_id": admin_id})
        if existing_config and consumer_secret == '*' * 20:
            consumer_secret = decrypt_mpesa_credentials(existing_config).get('consumer_secret', '')
        if existing_config and passkey == '*' * 20:
            passkey = decrypt_mpesa_credentials(existing_config).get('passkey', '')

        # Prepare credentials for encryption
        credentials = {
            'admin_id': admin_id,
            'consumer_key': consumer_key,
            'consumer_secret': consumer_secret,
            'shortcode': shortcode,
            'passkey': passkey,
            'environment': environment,
            'updated_at': datetime.now(),
            'callback_url': f"{request.url_root}mpesa/paybill_callback"
        }

        # Encrypt sensitive credentials
        encrypted_credentials = encrypt_mpesa_credentials(credentials)

        # Save to database
        if existing_config:
            mongo.db.mpesa_config.update_one(
                {"admin_id": admin_id},
                {"$set": encrypted_credentials}
            )
        else:
            mongo.db.mpesa_config.insert_one(encrypted_credentials)

        flash('M-Pesa configuration saved successfully!', 'success')
        app.logger.info(f"M-Pesa config updated for admin {admin_id}")

    except Exception as e:
        app.logger.error(f"Error saving M-Pesa config: {e}")
        flash(f'Error saving M-Pesa configuration: {str(e)}', 'danger')

    return redirect(url_for('sms_config'))


@app.route('/tenant_portal/<token>')
def tenant_portal(token):
    """Tenant portal for accessing reading history"""
    try:
        app.logger.info(f"Tenant portal access attempt with token: {token[:20]}...")
        # Verify token
        token_data = verify_tenant_access_token(token)
        if not token_data:
            app.logger.warning(f"Token verification failed for: {token[:20]}...")
            return render_template('error.html',
                                 error_title="Access Denied",
                                 error_message="Invalid or expired access link. Please contact your landlord for a new link."), 403

        tenant_id = token_data['tenant_id']
        admin_id = token_data['admin_id']

        # Get tenant information with property validation
        tenant = mongo.db.tenants.find_one({
            "_id": tenant_id,
            "admin_id": admin_id
        })

        if not tenant:
            return render_template('error.html',
                                 error_title="Tenant Not Found",
                                 error_message="Tenant record not found."), 404

        # CRITICAL: Get tenant's property_id for data separation
        property_id = tenant.get('property_id')
        if not property_id:
            app.logger.error(f"Tenant {tenant_id} has no property_id - data integrity issue")
            return render_template('error.html',
                                 error_title="Data Error",
                                 error_message="Tenant data configuration error. Please contact your landlord."), 500

        # Get tenant's reading history with proper property isolation
        readings = list(mongo.db.meter_readings.find({
            "tenant_id": tenant_id,
            "admin_id": admin_id,
            "property_id": property_id  # CRITICAL: Property-isolated access
        }).sort("date_recorded", -1).limit(12))  # Last 12 readings

        # Get payment information for each reading
        readings_with_payments = []
        for reading in readings:
            reading_month = reading['date_recorded'].strftime('%Y-%m')
            payment = mongo.db.payments.find_one({
                'tenant_id': tenant_id,
                'admin_id': admin_id,
                'property_id': property_id,  # CRITICAL: Property-isolated payment access
                'month_year': reading_month
            })

            # Add payment information to reading
            reading_data = dict(reading)
            if payment:
                amount_paid = payment.get('amount_paid', 0)
                outstanding = max(0, reading['bill_amount'] - amount_paid)
                reading_data['payment_status'] = 'Paid' if outstanding == 0 else ('Partial' if amount_paid > 0 else 'Unpaid')
                reading_data['amount_paid'] = amount_paid
                reading_data['outstanding'] = outstanding
                reading_data['payment_date'] = payment.get('payment_date')
            else:
                reading_data['payment_status'] = 'Unpaid'
                reading_data['amount_paid'] = 0
                reading_data['outstanding'] = reading['bill_amount']
                reading_data['payment_date'] = None

            readings_with_payments.append(reading_data)

        # Get admin/landlord information
        admin = mongo.db.admins.find_one({"_id": admin_id}, {"name": 1, "phone": 1})

        return render_template('tenant_portal.html',
                             tenant=tenant,
                             readings=readings_with_payments,
                             admin=admin,
                             token=token)

    except Exception as e:
        app.logger.error(f"Tenant portal error: {e}")
        return render_template('error.html',
                             error_title="System Error",
                             error_message="An error occurred while loading your data. Please try again later."), 500


@app.route('/tenant_download/<token>')
def tenant_download_history(token):
    """Allow tenant to download their reading history"""
    try:
        # Verify token
        token_data = verify_tenant_access_token(token)
        if not token_data:
            return render_template('error.html',
                                 error_title="Access Denied",
                                 error_message="Invalid or expired access link."), 403

        tenant_id = token_data['tenant_id']
        admin_id = token_data['admin_id']

        # Get tenant information with property validation
        tenant = mongo.db.tenants.find_one({
            "_id": tenant_id,
            "admin_id": admin_id
        })

        if not tenant:
            return render_template('error.html',
                                 error_title="Tenant Not Found",
                                 error_message="Tenant record not found."), 404

        # CRITICAL: Get tenant's property_id for data separation
        property_id = tenant.get('property_id')
        if not property_id:
            app.logger.error(f"Tenant {tenant_id} has no property_id - data integrity issue")
            return render_template('error.html',
                                 error_title="Data Error",
                                 error_message="Tenant data configuration error. Please contact your landlord."), 500

        # Get tenant's reading history with proper property isolation
        readings = list(mongo.db.meter_readings.find({
            "tenant_id": tenant_id,
            "admin_id": admin_id,
            "property_id": property_id  # CRITICAL: Property-isolated access
        }).sort("date_recorded", 1))

        # Create Excel file in memory
        output = BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})

        # Create worksheet
        worksheet = workbook.add_worksheet(f"My_Reading_History")

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
        worksheet.write('A1', 'My Water Usage History', header_format)
        worksheet.write('A2', 'Name:', cell_format)
        worksheet.write('B2', tenant['name'], cell_format)
        worksheet.write('A3', 'House Number:', cell_format)
        worksheet.write('B3', tenant['house_number'], cell_format)
        worksheet.write('A4', 'Generated:', cell_format)
        worksheet.write('B4', datetime.now().strftime('%Y-%m-%d %H:%M'), cell_format)

        # Write reading history headers
        headers = ['Date', 'Previous Reading (m³)', 'Current Reading (m³)', 'Usage (m³)', 'Bill Amount', 'Payment Status', 'Amount Paid', 'Outstanding']
        start_row = 6

        worksheet.write('A6', 'Reading History', header_format)
        for col, header in enumerate(headers):
            worksheet.write(start_row + 1, col, header, header_format)

        # Write reading data with payment information
        for row, reading in enumerate(readings, start_row + 2):
            # Get payment information for this reading
            reading_month = reading['date_recorded'].strftime('%Y-%m')
            payment = mongo.db.payments.find_one({
                'tenant_id': tenant_id,
                'admin_id': admin_id,
                'month_year': reading_month
            })

            # Determine payment status
            if payment:
                amount_paid = payment.get('amount_paid', 0)
                bill_amount = reading['bill_amount']
                outstanding = max(0, bill_amount - amount_paid)

                if outstanding == 0:
                    payment_status = 'Paid'
                elif amount_paid > 0:
                    payment_status = 'Partial'
                else:
                    payment_status = 'Unpaid'
            else:
                payment_status = 'Unpaid'
                amount_paid = 0
                outstanding = reading['bill_amount']

            worksheet.write(row, 0, reading['date_recorded'], date_format)
            worksheet.write(row, 1, reading['previous_reading'], cell_format)
            worksheet.write(row, 2, reading['current_reading'], cell_format)
            worksheet.write(row, 3, reading['usage'], cell_format)
            worksheet.write(row, 4, reading['bill_amount'], currency_format)
            worksheet.write(row, 5, payment_status, cell_format)
            worksheet.write(row, 6, amount_paid, currency_format)
            worksheet.write(row, 7, outstanding, currency_format)

        # Add summary statistics
        if readings:
            summary_row = start_row + len(readings) + 3
            worksheet.write(summary_row, 0, 'Summary Statistics', header_format)
            worksheet.write(summary_row + 1, 0, 'Total Readings:', cell_format)
            worksheet.write(summary_row + 1, 1, len(readings), cell_format)
            worksheet.write(summary_row + 2, 0, 'Total Usage:', cell_format)
            worksheet.write(summary_row + 2, 1, sum(r['usage'] for r in readings), cell_format)
            worksheet.write(summary_row + 3, 0, 'Total Billed:', cell_format)
            worksheet.write(summary_row + 3, 1, sum(r['bill_amount'] for r in readings), currency_format)

        # Auto-adjust column widths
        worksheet.set_column('A:A', 15)
        worksheet.set_column('B:D', 12)
        worksheet.set_column('E:H', 15)

        workbook.close()
        output.seek(0)

        # Mark token as used (one-time download)
        mark_token_as_used(token)

        # Return file
        filename = f"{tenant['name']}_Water_History_{datetime.now().strftime('%Y%m%d')}.xlsx"
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        app.logger.error(f"Tenant download error: {e}")
        return render_template('error.html',
                             error_title="Download Error",
                             error_message="Unable to generate your reading history. Please try again later."), 500


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

@app.route('/bulk_import_readings_excel', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def bulk_import_readings_excel():
    """Import bulk water readings from Excel file"""
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))

    if 'readings_excel_file' not in request.files:
        flash('No file selected', 'danger')
        return redirect(url_for('dashboard'))

    file = request.files['readings_excel_file']
    if file.filename == '':
        flash('No file selected', 'danger')
        return redirect(url_for('dashboard'))

    if not file.filename.lower().endswith(('.xlsx', '.xls')):
        flash('Invalid file format. Please upload an Excel file (.xlsx or .xls)', 'danger')
        return redirect(url_for('dashboard'))

    # Check SMS option
    send_sms = request.form.get('send_sms') == 'true'

    try:
        # Read Excel file
        df = pd.read_excel(file)

        # Validate columns
        required_columns = ['house_number', 'current_reading']
        if not all(col in df.columns for col in required_columns):
            flash(f'Excel file must contain columns: {", ".join(required_columns)}', 'danger')
            return redirect(url_for('dashboard'))

        results = {
            'total': len(df),
            'processed': 0,
            'errors': [],
            'warnings': [],
            'sms_sent': 0,
            'sms_failed': 0
        }

        # Process each row
        for index, row in df.iterrows():
            try:
                house_number = str(row['house_number']).strip().upper()
                current_reading = float(row['current_reading'])

                if pd.isna(current_reading) or current_reading < 0:
                    results['errors'].append(f"Row {index + 2}: Invalid reading value")
                    continue

                # Process the reading
                result = process_bulk_reading(admin_id, house_number, current_reading, send_sms)
                if result['success']:
                    results['processed'] += 1
                    if result.get('warning'):
                        results['warnings'].append(f"Row {index + 2}: {result['warning']}")

                    # Track SMS status
                    if send_sms:
                        sms_status = result.get('sms_status', 'not_sent')
                        if sms_status == 'sent':
                            results['sms_sent'] += 1
                        elif 'failed' in sms_status or 'error' in sms_status:
                            results['sms_failed'] += 1
                else:
                    results['errors'].append(f"Row {index + 2}: {result['error']}")

            except Exception as e:
                results['errors'].append(f"Row {index + 2}: {str(e)}")

        # Flash results
        if results['processed'] > 0:
            success_msg = f'Successfully processed {results["processed"]} out of {results["total"]} readings'
            if send_sms:
                success_msg += f'. SMS sent: {results["sms_sent"]}, Failed: {results["sms_failed"]}'
            flash(success_msg, 'success')

        if results['warnings']:
            for warning in results['warnings'][:5]:  # Show first 5 warnings
                flash(f'Warning: {warning}', 'warning')
            if len(results['warnings']) > 5:
                flash(f'... and {len(results["warnings"]) - 5} more warnings', 'warning')

        if results['errors']:
            for error in results['errors'][:5]:  # Show first 5 errors
                flash(f'Error: {error}', 'danger')
            if len(results['errors']) > 5:
                flash(f'... and {len(results["errors"]) - 5} more errors', 'danger')

    except Exception as e:
        app.logger.error(f"Bulk readings Excel import error: {e}")
        flash(f'Error processing Excel file: {str(e)}', 'danger')

    return redirect(url_for('dashboard'))

@app.route('/bulk_import_readings_text', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def bulk_import_readings_text():
    """Import bulk water readings from text file"""
    try:
        admin_id = get_admin_id()
    except ValueError:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))

    if 'readings_text_file' not in request.files:
        flash('No file selected', 'danger')
        return redirect(url_for('dashboard'))

    file = request.files['readings_text_file']
    if file.filename == '':
        flash('No file selected', 'danger')
        return redirect(url_for('dashboard'))

    if not file.filename.lower().endswith('.txt'):
        flash('Invalid file format. Please upload a text file (.txt)', 'danger')
        return redirect(url_for('dashboard'))

    # Check SMS option
    send_sms = request.form.get('send_sms') == 'true'

    try:
        # Read text file
        content = file.read().decode('utf-8')
        lines = [line.strip() for line in content.splitlines() if line.strip()]

        results = {
            'total': len(lines),
            'processed': 0,
            'errors': [],
            'warnings': [],
            'sms_sent': 0,
            'sms_failed': 0
        }

        # Process each line
        for line_num, line in enumerate(lines, 1):
            try:
                # Parse line format: "house_number reading"
                parts = line.split()
                if len(parts) != 2:
                    results['errors'].append(f"Line {line_num}: Invalid format. Expected 'house_number reading'")
                    continue

                house_number = parts[0].strip().upper()
                current_reading = float(parts[1])

                if current_reading < 0:
                    results['errors'].append(f"Line {line_num}: Reading cannot be negative")
                    continue

                # Process the reading
                result = process_bulk_reading(admin_id, house_number, current_reading, send_sms)
                if result['success']:
                    results['processed'] += 1
                    if result.get('warning'):
                        results['warnings'].append(f"Line {line_num}: {result['warning']}")

                    # Track SMS status
                    if send_sms:
                        sms_status = result.get('sms_status', 'not_sent')
                        if sms_status == 'sent':
                            results['sms_sent'] += 1
                        elif 'failed' in sms_status or 'error' in sms_status:
                            results['sms_failed'] += 1
                else:
                    results['errors'].append(f"Line {line_num}: {result['error']}")

            except ValueError:
                results['errors'].append(f"Line {line_num}: Invalid reading value")
            except Exception as e:
                results['errors'].append(f"Line {line_num}: {str(e)}")

        # Flash results
        if results['processed'] > 0:
            success_msg = f'Successfully processed {results["processed"]} out of {results["total"]} readings'
            if send_sms:
                success_msg += f'. SMS sent: {results["sms_sent"]}, Failed: {results["sms_failed"]}'
            flash(success_msg, 'success')

        if results['warnings']:
            for warning in results['warnings'][:5]:  # Show first 5 warnings
                flash(f'Warning: {warning}', 'warning')
            if len(results['warnings']) > 5:
                flash(f'... and {len(results["warnings"]) - 5} more warnings', 'warning')

        if results['errors']:
            for error in results['errors'][:5]:  # Show first 5 errors
                flash(f'Error: {error}', 'danger')
            if len(results['errors']) > 5:
                flash(f'... and {len(results["errors"]) - 5} more errors', 'danger')

    except Exception as e:
        app.logger.error(f"Bulk readings text import error: {e}")
        flash(f'Error processing text file: {str(e)}', 'danger')

    return redirect(url_for('dashboard'))

def process_bulk_reading(admin_id, house_number, current_reading, send_sms=True):
    """Process a single bulk reading entry"""
    try:
        # Find tenant by house number
        tenant = mongo.db.tenants.find_one({
            "house_number": house_number,
            "admin_id": admin_id
        })

        if not tenant:
            return {
                'success': False,
                'error': f'No tenant found for house number {house_number}'
            }

        tenant_id = tenant['_id']

        # Get the house document to retrieve house_id
        house = mongo.db.houses.find_one({"house_number": house_number, "admin_id": admin_id})
        house_id = house.get('_id') if house else None

        # Get the latest reading for this house number
        latest_house_reading = get_last_house_reading(house_number, admin_id)

        # Determine previous reading
        if latest_house_reading:
            previous_reading = latest_house_reading.get('current_reading', 0)
            # Check if reading is decreasing
            if current_reading < previous_reading:
                return {
                    'success': False,
                    'error': f'Current reading ({current_reading}) is less than previous reading ({previous_reading})'
                }
        else:
            previous_reading = 0

        # Calculate usage and bill using property-specific rate
        usage = max(0, current_reading - previous_reading)
        # Get property_id from tenant record
        property_id = tenant.get('property_id')
        rate_per_unit = get_rate_per_unit(admin_id, property_id)
        bill_amount = usage * rate_per_unit

        # Create reading record
        reading_date = datetime.now()
        reading_data = {
            "tenant_id": tenant_id,
            "house_number": house_number,
            "previous_reading": previous_reading,
            "current_reading": current_reading,
            "usage": usage,
            "bill_amount": bill_amount,
            "date_recorded": reading_date,
            "admin_id": admin_id,
            "tenant_name": tenant['name'],
            "current_tenant_id": tenant_id,
            "reading_type": "bulk_import",
            "source_collection": "meter_readings"
        }

        if house_id:
            reading_data["house_id"] = house_id

        # Insert reading
        result = mongo.db.meter_readings.insert_one(reading_data)
        reading_id = result.inserted_id

        # Create payment record
        month_year = reading_date.strftime('%Y-%m')
        create_payment_record(admin_id, tenant_id, house_id, bill_amount, reading_id, month_year, 'water')

        warning_msg = None
        if usage == 0:
            warning_msg = f'No usage recorded for {house_number} (same reading as previous)'

        # Send SMS notification if enabled
        sms_status = 'not_sent'
        if send_sms and tenant.get('phone'):
            try:
                # Get admin info for SMS
                admin = mongo.db.admins.find_one({"_id": admin_id})
                admin_name = admin.get('name', 'Your Landlord') if admin else 'Your Landlord'
                admin_phone = admin.get('phone', '') if admin else ''

                # Get payment info
                payment_method = admin.get('payment_method', 'till') if admin else 'till'
                if payment_method == 'till':
                    till = admin.get('till', '') if admin else ''
                    payment_info = f"Pay via Till: {till}" if till else "Contact landlord for payment details"
                elif payment_method == 'paybill':
                    business_number = admin.get('business_number', '') if admin else ''
                    account_name = admin.get('account_name', house_number) if admin else house_number
                    payment_info = f"Pay via Paybill: {business_number}, Account: {account_name}" if business_number else "Contact landlord for payment details"
                else:
                    payment_info = "Contact landlord for payment details"

                # Calculate arrears excluding current month's bill
                current_month_year = get_current_month_year()
                total_arrears = calculate_total_arrears(admin_id, tenant_id, bill_type='water', exclude_current_month=current_month_year)

                # Generate secure access token for tenant portal
                access_token = generate_tenant_access_token(tenant_id, admin_id, expires_in_hours=24)
                long_portal_link = f"https://{request.host}/tenant_portal/{access_token}"
                portal_link = shorten_url(long_portal_link, f"tenant_{tenant_id}_{datetime.now().strftime('%Y%m')}")

                # Create message based on arrears
                if total_arrears > 1:
                    message = (
                        f"Water Bill Alert: {tenant['name']}, House {house_number}. "
                        f"Current bill: KES {bill_amount:.2f}. "
                        f"Outstanding arrears: KES {total_arrears:.2f}. "
                        f"{payment_info} "
                        f"View your usage history: {portal_link} "
                        f"From {admin_name} - {admin_phone}"
                    )
                else:
                    message = (
                        f"Water Bill Alert: {tenant['name']}, House {house_number}. "
                        f"Current bill: KES {bill_amount:.2f}. "
                        f"{payment_info} "
                        f"View history: {portal_link} "
                        f"From {admin_name} - {admin_phone}"
                    )

                # Send SMS
                response = send_message(tenant['phone'], message)

                if "error" in response:
                    sms_status = f"failed: {response['error']}"
                    warning_msg = f"{warning_msg}, SMS failed" if warning_msg else "SMS sending failed"
                else:
                    sms_status = "sent"

                # Update reading with SMS status
                mongo.db.meter_readings.update_one(
                    {"_id": reading_id},
                    {"$set": {"sms_status": sms_status}}
                )

            except Exception as sms_error:
                sms_status = f"error: {str(sms_error)}"
                warning_msg = f"{warning_msg}, SMS error" if warning_msg else "SMS sending error"
                app.logger.error(f"SMS error for bulk reading {house_number}: {sms_error}")

        return {
            'success': True,
            'reading_id': reading_id,
            'usage': usage,
            'bill_amount': bill_amount,
            'warning': warning_msg,
            'sms_status': sms_status
        }

    except Exception as e:
        app.logger.error(f"Error processing bulk reading for {house_number}: {e}")
        return {
            'success': False,
            'error': str(e)
        }

@app.route('/download_bulk_readings_template')
@login_required
def download_bulk_readings_template():
    """Download Excel template for bulk readings import"""
    try:
        # Create a BytesIO object
        output = BytesIO()

        # Create workbook and worksheet
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet('Bulk Readings Template')

        # Create formats
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#D7E4BC',
            'border': 1,
            'text_wrap': True,
            'valign': 'top'
        })

        cell_format = workbook.add_format({
            'border': 1,
            'text_wrap': True,
            'valign': 'top'
        })

        # Write headers
        headers = ['house_number', 'current_reading']
        for col, header in enumerate(headers):
            worksheet.write(0, col, header, header_format)

        # Write sample data
        sample_data = [
            ['A08', 30],
            ['B12', 45.5],
            ['C05', 25],
            ['D14', 38]
        ]

        for row, data in enumerate(sample_data, 1):
            for col, value in enumerate(data):
                worksheet.write(row, col, value, cell_format)

        # Set column widths
        worksheet.set_column('A:A', 15)
        worksheet.set_column('B:B', 18)

        # Add instructions
        worksheet.write('A7', 'Instructions:', header_format)
        worksheet.write('A8', '1. Fill in house_number column with exact house numbers', cell_format)
        worksheet.write('A9', '2. Fill in current_reading column with meter readings', cell_format)
        worksheet.write('A10', '3. Save the file and upload through the dashboard', cell_format)
        worksheet.write('A11', '4. System will calculate usage automatically', cell_format)

        workbook.close()
        output.seek(0)

        return send_file(
            output,
            as_attachment=True,
            download_name='bulk_readings_template.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        app.logger.error(f"Error generating bulk readings template: {e}")
        flash('Error generating template file', 'danger')
        return redirect(url_for('dashboard'))

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

@app.route('/generate_analytics_report')
@login_required
def generate_analytics_report():
    """Generate comprehensive analytics report."""
    try:
        admin_id = get_admin_id()

        # Create Excel workbook
        output = BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})

        # Create header format
        header_format = workbook.add_format({
            'bold': True,
            'font_color': 'white',
            'bg_color': '#4472C4',
            'border': 1
        })

        # Create data format
        data_format = workbook.add_format({
            'border': 1,
            'align': 'center'
        })

        # Create currency format
        currency_format = workbook.add_format({
            'num_format': 'KES #,##0.00',
            'border': 1,
            'align': 'right'
        })

        # Summary Sheet
        summary_sheet = workbook.add_worksheet('Analytics Summary')
        analytics_data = calculate_dashboard_analytics(admin_id)

        summary_sheet.write(0, 0, 'Analytics Summary Report', workbook.add_format({'bold': True, 'font_size': 16}))
        summary_sheet.write(1, 0, f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        summary_sheet.write(3, 0, 'Metric', header_format)
        summary_sheet.write(3, 1, 'Value', header_format)

        summary_sheet.write(4, 0, 'Monthly Consumption (m³)', data_format)
        summary_sheet.write(4, 1, analytics_data['monthly_consumption'], data_format)

        summary_sheet.write(5, 0, 'Total Revenue (KES)', data_format)
        summary_sheet.write(5, 1, analytics_data['total_revenue'], currency_format)

        summary_sheet.write(6, 0, 'Average Usage per Tenant (m³)', data_format)
        summary_sheet.write(6, 1, analytics_data['avg_usage'], data_format)

        tenant_count = mongo.db.tenants.count_documents({"admin_id": admin_id})
        summary_sheet.write(7, 0, 'Total Tenants', data_format)
        summary_sheet.write(7, 1, tenant_count, data_format)

        # Monthly Trends Sheet
        trends_sheet = workbook.add_worksheet('Monthly Trends')

        # Get monthly data for the past 12 months
        twelve_months_ago = datetime.now() - timedelta(days=365)
        monthly_trends = list(mongo.db.meter_readings.aggregate([
            {"$match": {"admin_id": admin_id, "date_recorded": {"$gte": twelve_months_ago}}},
            {"$group": {
                "_id": {
                    "year": {"$year": "$date_recorded"},
                    "month": {"$month": "$date_recorded"}
                },
                "total_usage": {"$sum": "$usage"},
                "total_revenue": {"$sum": "$bill_amount"},
                "reading_count": {"$sum": 1}
            }},
            {"$sort": {"_id.year": 1, "_id.month": 1}}
        ]))

        trends_sheet.write(0, 0, 'Month', header_format)
        trends_sheet.write(0, 1, 'Total Usage (m³)', header_format)
        trends_sheet.write(0, 2, 'Total Revenue (KES)', header_format)
        trends_sheet.write(0, 3, 'Number of Readings', header_format)

        for i, trend in enumerate(monthly_trends, 1):
            month_str = f"{trend['_id']['year']}-{trend['_id']['month']:02d}"
            trends_sheet.write(i, 0, month_str, data_format)
            trends_sheet.write(i, 1, trend['total_usage'], data_format)
            trends_sheet.write(i, 2, trend['total_revenue'], currency_format)
            trends_sheet.write(i, 3, trend['reading_count'], data_format)

        # Top Consumers Sheet
        consumers_sheet = workbook.add_worksheet('Top Consumers')

        top_consumers = list(mongo.db.meter_readings.aggregate([
            {"$match": {"admin_id": admin_id}},
            {"$lookup": {
                "from": "tenants",
                "localField": "tenant_id",
                "foreignField": "_id",
                "as": "tenant_info"
            }},
            {"$unwind": "$tenant_info"},
            {"$group": {
                "_id": "$tenant_id",
                "name": {"$first": "$tenant_info.name"},
                "house_number": {"$first": "$tenant_info.house_number"},
                "total_usage": {"$sum": "$usage"},
                "total_revenue": {"$sum": "$bill_amount"},
                "reading_count": {"$sum": 1}
            }},
            {"$sort": {"total_usage": -1}},
            {"$limit": 20}
        ]))

        consumers_sheet.write(0, 0, 'Rank', header_format)
        consumers_sheet.write(0, 1, 'Name', header_format)
        consumers_sheet.write(0, 2, 'House Number', header_format)
        consumers_sheet.write(0, 3, 'Total Usage (m³)', header_format)
        consumers_sheet.write(0, 4, 'Total Revenue (KES)', header_format)
        consumers_sheet.write(0, 5, 'Number of Readings', header_format)

        for i, consumer in enumerate(top_consumers, 1):
            consumers_sheet.write(i, 0, i, data_format)
            consumers_sheet.write(i, 1, consumer['name'], data_format)
            consumers_sheet.write(i, 2, consumer['house_number'], data_format)
            consumers_sheet.write(i, 3, consumer['total_usage'], data_format)
            consumers_sheet.write(i, 4, consumer['total_revenue'], currency_format)
            consumers_sheet.write(i, 5, consumer['reading_count'], data_format)

        # Set column widths
        for sheet in [summary_sheet, trends_sheet, consumers_sheet]:
            for col in range(6):
                sheet.set_column(col, col, 15)

        workbook.close()
        output.seek(0)

        filename = f"analytics_report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        app.logger.error(f"Error generating analytics report: {e}")
        flash('Error generating report. Please try again.', 'danger')
        return redirect(url_for('dashboard'))

#download data of all of your tenants
# Route to download Excel template

# Error handlers for better user experience
@app.errorhandler(404)
def not_found_error(error):
    """Handle 404 errors"""
    app.logger.warning(f"404 error: {request.url}")
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    app.logger.error(f"500 error: {error}")
    return render_template('500.html'), 500

@app.errorhandler(Exception)
def handle_exception(e):
    """Handle unexpected exceptions"""
    app.logger.error(f"Unhandled exception: {e}")
    # Don't reveal error details in production
    if app.debug:
        return f"<h1>Error</h1><p>{str(e)}</p>", 500
    else:
        return render_template('error.html'), 500

# CSRF Error Handlers for Enhanced Security
@csrf.error_handler
def csrf_error(reason):
    """Handle CSRF token validation errors"""
    app.logger.warning(f"CSRF validation failed: {reason}")

    # Check if this is an AJAX request
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'error': 'CSRF token validation failed',
            'message': 'Security token expired or invalid. Please refresh the page.',
            'csrf_error': True
        }), 400

    # For regular form submissions
    flash('Security token expired or invalid. Please try again.', 'warning')

    # Redirect back to the referring page or dashboard
    referrer = request.referrer
    if referrer and referrer.startswith(request.host_url):
        return redirect(referrer)
    else:
        return redirect(url_for('dashboard'))

@app.errorhandler(400)
def bad_request_error(error):
    """Handle 400 bad request errors (including CSRF failures)"""
    app.logger.warning(f"400 error: {error}")

    # Check if this might be a CSRF error
    if 'csrf' in str(error).lower() or 'token' in str(error).lower():
        flash('Security validation failed. Please try again.', 'warning')
        return redirect(url_for('dashboard'))

    return render_template('error.html',
                         error_message="Bad request. Please check your input."), 400

# Database health check route
@app.route('/health')
def health_check():
    """Simple health check endpoint"""
    try:
        # Check database connection
        if not is_db_connected():
            return jsonify({
                'status': 'unhealthy',
                'database': 'disconnected'
            }), 503

        return jsonify({
            'status': 'healthy',
            'database': 'connected'
        }), 200
    except Exception as e:
        app.logger.error(f"Health check failed: {e}")
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 503

if __name__ == '__main__':
    try:
        create_admin_user()
    except Exception as e:
        app.logger.error(f"Database initialization error: {e}")
        print(f"Database initialization error: {e}")
    
    app.run(debug=True, host='0.0.0.0', port=5000)
