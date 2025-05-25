import os
import logging
import urllib.parse
import re
import requests
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
DATABASE_NAME = os.getenv("DATABASE_NAME", "Cluster0")

# Create Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
app.config['MONGO_URI'] = MONGO_URI
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
                            "created_at": datetime.utcnow()
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
                    'payment_date': None,
                    'payment_method': None,
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

# Call this function when the app starts
if mongo:
    with app.app_context():
        initialize_houses_collection()
        migrate_existing_data()
        migrate_existing_readings_to_payments()
    app.logger.info("Houses collection initialized successfully")



def get_admin_id():
    """Get admin ID from session with validation."""
    try:
        return ObjectId(session['admin_id'])
    except (KeyError, TypeError):
        raise ValueError("Invalid admin session")


# Function to send SMS with TalkSasa
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

def create_payment_record(admin_id, tenant_id, house_id, bill_amount, reading_id, month_year):
    """Create a new payment record when a water reading is recorded"""
    try:
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
            'payment_date': None,
            'payment_method': None,
            'notes': '',
            'created_at': datetime.now(),
            'updated_at': datetime.now()
        }
        
        result = mongo.db.payments.insert_one(payment_data)
        cache.delete_memoized(get_billing_summary, admin_id)
        return result.inserted_id
    except Exception as e:
        app.logger.error(f"Error creating payment record: {str(e)}")
        return None

def get_unpaid_bills(admin_id, tenant_id=None):
    """Get all unpaid bills for admin or specific tenant"""
    try:
        query = {
            'admin_id': ObjectId(admin_id),
            'payment_status': {'$in': ['unpaid', 'partial']}
        }
        
        if tenant_id:
            query['tenant_id'] = ObjectId(tenant_id)
            
        unpaid_bills = list(mongo.db.payments.find(query).sort('due_date', 1))
        return unpaid_bills
    except Exception as e:
        app.logger.error(f"Error fetching unpaid bills: {str(e)}")
        return []

def get_unpaid_bills_paginated(admin_id, page=1, per_page=10, filter_status=None, search_term=None):
    """Get paginated unpaid bills with optional filtering"""
    try:
        # Start with basic query
        query = {'admin_id': ObjectId(admin_id)}
        
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
            'payment_date': datetime.now(),
            'payment_method': payment_method,
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

def calculate_total_arrears(admin_id, tenant_id):
    """Calculate total arrears for a tenant"""
    try:
        unpaid_bills = get_unpaid_bills(admin_id, tenant_id)
        total_arrears = 0
        
        for bill in unpaid_bills:
            outstanding = bill['bill_amount'] - bill.get('amount_paid', 0)
            total_arrears += outstanding
            
        return total_arrears
    except Exception as e:
        app.logger.error(f"Error calculating arrears: {str(e)}")
        return 0

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
        name = request.form.get('name', '').strip()
        till = request.form.get('till', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm-password', '')
        cost = request.form.get('cost', type=float)
        
        # Validate inputs
        if not all([name, till, password, confirm_password, cost]):
            flash('All fields are required', 'danger')
            return render_template('signup.html')
            
        if password != confirm_password:
            flash('Passwords do not match', 'danger')
            return render_template('signup.html')
            
        # Check if till already exists
        existing_admin = mongo.db.admins.find_one({"till": till})
        if existing_admin:
            flash('An account with this till number already exists', 'danger')
            return render_template('signup.html')
            
        # Create new admin with isolated data structure
        hashed_password = generate_password_hash(password)
        new_admin = {
            "name": name,
            "till": till,
            "username": name,  
            "password": hashed_password,
            "rate_per_unit": cost,
            "created_at": datetime.utcnow(),
            "tenants": [],  # Empty array to store this admin's tenants
            "houses": [],   # Empty array to store this admin's houses
            "readings": []  # Empty array to store this admin's readings
        }
        
        try:
            # Insert the new admin
            result = mongo.db.admins.insert_one(new_admin)
            
            # Create SMS config for this admin
            sms_config = {
                "admin_id": result.inserted_id,
                "rate_per_unit": cost,
                "created_at": datetime.utcnow()
            }
            mongo.db.sms_config.insert_one(sms_config)
            
            flash('Account created successfully! You can now log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            app.logger.error(f"Error creating account: {e}")
            flash('An error occurred while creating your account. Please try again.', 'danger')
            
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
    
    readings = list(mongo.db.water_readings.aggregate(readings_pipeline))
    
    # Format readings for template
    formatted_readings = []
    for reading in readings:
        reading['tenant_info']['id'] = str(reading['tenant_info']['_id'])
        formatted_readings.append((reading, reading['tenant_info']))
    
    # Get rate per unit
    rate_per_unit = get_rate_per_unit(admin_id)
    
    return render_template(
        'dashboard.html',
        tenants=tenants,
        pagination=pagination,
        readings=formatted_readings,
        search_query=search_query,
        search_type=search_type,
        rate_per_unit=rate_per_unit
    )

@app.route('/tenant/<tenant_id>')
@login_required
def tenant_details(tenant_id):
    # Convert string ID to ObjectId
    admin_id = ObjectId(session['admin_id'])

    tenant_id_obj = ObjectId(tenant_id)
    tenant = mongo.db.tenants.find_one({"_id": tenant_id_obj, "admin_id": admin_id})    
    if not tenant:
        flash('Tenant not found', 'danger')
        return redirect(url_for('dashboard'))

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', DEFAULT_PER_PAGE, type=int)
    skip = (page - 1) * per_page

    # For table display, use descending order
    readings_count = mongo.db.water_readings.count_documents({"tenant_id": tenant_id_obj, "admin_id": admin_id})    
    readings = list(mongo.db.water_readings.find(
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
    chart_readings = list(mongo.db.water_readings.find(
            {"tenant_id": tenant_id_obj, "admin_id": admin_id}
        ).sort("date_recorded", 1))

    labels = [r['date_recorded'].strftime('%Y-%m-%d') for r in chart_readings]
    usage_data = [r['usage'] for r in chart_readings]
    bill_data = [r['bill_amount'] for r in chart_readings]

    # Get latest reading for the form
    latest_reading = None
    if readings:
        latest_reading = max(readings, key=lambda x: x['date_recorded'])
    
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
    admin_id = ObjectId(session['admin_id'])
    tenant_id_obj = ObjectId(tenant_id)
    tenant = mongo.db.tenants.find_one({"_id": tenant_id_obj, "admin_id": admin_id})    

    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404
    
    house_number = tenant.get('house_number')
    
    # First, check if there are any readings for this tenant
    tenant_readings = list(mongo.db.water_readings.find(
        {"tenant_id": tenant_id_obj, "admin_id": admin_id}
    ).sort("date_recorded", 1))
    
    # If no tenant readings, check for house readings
    if not tenant_readings and house_number:
        # Look for the latest reading for this house number WITH admin_id filter
        house_reading = mongo.db.house_readings.find_one(
            {"house_number": house_number, "admin_id": admin_id},
            sort=[("date_recorded", -1)]
        )
        
        if house_reading:
            # Convert the house reading to the expected format
            data = [{
                'date': house_reading['date_recorded'].strftime('%Y-%m-%d'),
                'previous_reading': house_reading['previous_reading'],
                'current_reading': house_reading['current_reading'],
                'usage': house_reading['usage'],
                'bill_amount': house_reading['bill_amount'],
                'sms_status': house_reading.get('sms_status', 'not_sent')
            }]
            return jsonify(data)
    
    # If we have tenant readings or no house readings, return the tenant readings
    data = []
    for reading in tenant_readings:
        data.append({
            'date': reading['date_recorded'].strftime('%Y-%m-%d'),
            'previous_reading': reading['previous_reading'],
            'current_reading': reading['current_reading'],
            'usage': reading['usage'],
            'bill_amount': reading['bill_amount'],
            'sms_status': reading.get('sms_status', 'not_sent')
        })
    
    return jsonify(data)

@app.route('/add_tenant', methods=['POST'])
@login_required
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
            "created_at": datetime.utcnow()
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
                "created_at": datetime.utcnow()
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
            readings = list(mongo.db.water_readings.find({"tenant_id": tenant_id_obj, "admin_id": admin_id}))
            
            # Create a house_readings collection if it doesn't exist
            if 'house_readings' not in mongo.db.list_collection_names():
                mongo.db.create_collection('house_readings')
            
            # For each reading, store it in the house_readings collection associated with the old house
            for reading in readings:
                # Check if this reading already exists in house_readings
                existing = mongo.db.house_readings.find_one({
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
                    
                    mongo.db.house_readings.insert_one(house_reading)
            
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
                    "created_at": datetime.utcnow()
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
            mongo.db.water_readings.delete_many({"tenant_id": tenant_id_obj})
            
            flash(f'Tenant "{tenant_name}" has been transferred from house {current_house} to house {new_house}. Reading history for both houses has been preserved.', 'success')
            return redirect(url_for('dashboard'))
            
        return render_template('transfer_tenant.html', tenant=tenant, houses=available_houses)
        
    except Exception as e:
        app.logger.error(f"Error transferring tenant: {e}")
        flash(f'Error: {str(e)}', 'danger')
        return redirect(url_for('dashboard'))

def get_last_house_reading(house_number, admin_id):
    """Get the last reading for a specific house number."""
    if not house_number:
        return None
        
    last_reading = mongo.db.house_readings.find_one(
        {"house_number": house_number, "admin_id": admin_id},
        sort=[("date_recorded", -1)]
    )
    
    return last_reading

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
        current_time = datetime.utcnow()
        
        # Create tenant reading record (for billing purposes)
        reading_data = {
            "tenant_id": tenant_id_obj,
            "house_number": house_number,
            "house_id": house_id,  # Add house_id to tenant readings
            "previous_reading": previous_reading,
            "current_reading": current_reading,
            "usage": usage,
            "bill_amount": bill_amount,
            "date_recorded": current_time,
            "sms_status": "pending",
            "admin_id": admin_id
        }
        
        # Insert reading
        result = mongo.db.water_readings.insert_one(reading_data)
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
        house_reading = {
            "house_number": house_number,
            "house_id": house_id,  # Add house_id to house readings
            "previous_reading": previous_reading,
            "current_reading": current_reading,
            "usage": usage,
            "bill_amount": bill_amount,
            "date_recorded": current_time,
            "tenant_name": tenant['name'],
            "tenant_id": tenant_id_obj,
            "current_tenant_id": tenant_id_obj,  # Explicitly store current tenant ID
            "admin_id": admin_id
        }
        mongo.db.house_readings.insert_one(house_reading)
        
        # Prepare and send SMS with arrears information - ENHANCED
        admin = mongo.db.admins.find_one({"_id": admin_id})
        admin_name = admin.get('name', 'Your Landlord') if admin else 'Your Landlord'
        admin_phone = admin.get('phone', 'N/A') if admin else 'N/A'
        till_number = admin.get('till', 'N/A') if admin else 'N/A'
        
        # Calculate arrears - ADDED
        total_arrears = calculate_total_arrears(admin_id, tenant_id_obj)
        
        # Create SMS message with arrears info - ENHANCED
        if total_arrears > 0:
            message = (
                f"Water Bill Alert: {tenant['name']}, House {house_number}. "
                f"Current bill: KES {bill_amount:.2f}. "
                f"Outstanding arrears: KES {total_arrears:.2f}. "
                f"Total due: KES {bill_amount + total_arrears:.2f}. "
                f"Pay via Till: {till_number} or {admin_name} - {admin_phone}"
            )
        else:
            message = (
                f"Water Bill Alert: {tenant['name']}, House {house_number}. "
                f"Current bill: KES {bill_amount:.2f}. "
                f"Pay via Till: {till_number} or {admin_name} - {admin_phone}"
            )
        
        # Send SMS (assuming send_message function exists)
        try:
            response = send_message(tenant['phone'], message)
            
            if "error" in response:
                mongo.db.water_readings.update_one(
                    {"_id": reading_id},
                    {"$set": {"sms_status": f"failed: {response['error']}"}}
                )
                flash(f"Reading recorded but SMS failed: {response['error']}", "warning")
            else:
                mongo.db.water_readings.update_one(
                    {"_id": reading_id},
                    {"$set": {"sms_status": "sent"}}
                )
                flash("Reading recorded and SMS sent successfully!", "success")
                
        except Exception as sms_error:
            app.logger.error(f"SMS error: {sms_error}")
            mongo.db.water_readings.update_one(
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
            reading_date = datetime.utcnow()
            
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
            "admin_id": admin_id
        }
        
        # Insert tenant reading
        result = mongo.db.water_readings.insert_one(reading_data)
        reading_id = result.inserted_id
        
        # Create payment record for this bill - FIXED
        month_year = reading_date.strftime('%Y-%m')
        payment_id = create_payment_record(
            admin_id=admin_id,  # Fixed: use admin_id
            tenant_id=tenant_id_obj,  # Fixed: use tenant_id_obj
            house_id=house_id,  # Fixed: use house_id
            bill_amount=bill_amount,
            reading_id=reading_id,  # Fixed: use reading_id
            month_year=month_year
        )
        
        if payment_id:
            app.logger.info(f"Payment record created with ID: {payment_id}")
        else:
            app.logger.error("Failed to create payment record")
        
        # Create house reading record (for house history)
        house_reading_data = {
            "house_number": house_number,
            "house_id": house_id,
            "current_tenant_id": tenant_id_obj,
            "current_tenant_name": tenant['name'],
            "previous_reading": previous_reading,
            "current_reading": current_reading,
            "usage": usage,
            "bill_amount": bill_amount,
            "date_recorded": reading_date,
            "admin_id": admin_id
        }
        
        # Insert house reading
        mongo.db.house_readings.insert_one(house_reading_data)

        admin = mongo.db.admins.find_one({"_id": admin_id})
        admin_name = admin.get('name', 'Your Landlord') if admin else 'Your Landlord'
        admin_phone = admin.get('phone', 'N/A') if admin else 'N/A'
        till_number = admin.get('till', 'N/A') if admin else 'N/A'
        
        # Calculate arrears and create enhanced SMS message - ADDED
        total_arrears = calculate_total_arrears(admin_id, tenant_id_obj)
        
        if total_arrears > 0:
            message = (
                f"Water Bill Alert: {tenant['name']}, House {house_number}. "
                f"Current bill: KES {bill_amount:.2f}. "
                f"Outstanding arrears: KES {total_arrears:.2f}. "
                f"Total due: KES {bill_amount + total_arrears:.2f}. "
                f"Pay via Till: {till_number} or {admin_name} - {admin_phone}"
            )
        else:
            message = (
                f"Water Bill Alert: {tenant['name']}, House {house_number}. "
                f"Current bill: KES {bill_amount:.2f}. "
                f"Pay via Till: {till_number} or {admin_name} - {admin_phone}"
            )
        
        # Send SMS notification
        try:
            response = send_message(tenant['phone'], message)
            
            if "error" in response:
                mongo.db.water_readings.update_one(
                    {"_id": reading_id},
                    {"$set": {"sms_status": f"failed: {response['error']}"}}
                )
                flash(f"Reading recorded but SMS failed: {response['error']}", "warning")
            else:
                mongo.db.water_readings.update_one(
                    {"_id": reading_id},
                    {"$set": {"sms_status": "sent"}}
                )
                flash("Reading recorded and SMS sent successfully!", "success")
                
        except Exception as sms_error:
            app.logger.error(f"SMS error: {sms_error}")
            mongo.db.water_readings.update_one(
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

@cache.memoize(timeout=3600)  # Cache for 1 hour
def get_billing_summary(admin_id):
    """Get billing summary using MongoDB aggregation pipeline"""
    try:
        # Ensure admin_id is an ObjectId
        if isinstance(admin_id, str):
            admin_id = ObjectId(admin_id)
            
        pipeline = [
            {'$match': {'admin_id': admin_id}},
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
        admin_id = ObjectId(session['admin_id'])

        # Get pagination parameters from request
        page = request.args.get('page', 1, type=int)
        filter_status = request.args.get('filter', 'unpaid_partial')
        search_term = request.args.get('search', '')
        
        # Get billing summary using aggregation pipeline
        billing_summary = get_billing_summary(admin_id)
        pagination = get_unpaid_bills_paginated(
            admin_id, page=page, per_page=10, 
            filter_status=filter_status, search_term=search_term
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
                               pagination=pagination)
        
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
    """Record a payment for a specific bill"""
    try:
        amount_paid = float(request.form.get('amount_paid', 0))
        payment_method = request.form.get('payment_method', 'cash')
        notes = request.form.get('notes', '')
        
        if amount_paid <= 0:
            flash('Payment amount must be greater than 0', 'danger')
            return redirect(url_for('payments_dashboard'))
        
        success = update_payment_status(payment_id, amount_paid, payment_method, notes)
        
        if success:
            admin_id = ObjectId(session.get('admin_id'))
            if admin_id:
                # Explicitly invalidate the cached billing summary
                cache.delete_memoized(get_billing_summary, admin_id)
                # Also invalidate with string version of admin_id for consistency
                cache.delete_memoized(get_billing_summary, str(admin_id))
            flash('Payment recorded successfully', 'success')
        else:
            flash('Error recording payment', 'danger')
            
    except ValueError:
        flash('Invalid payment amount', 'danger')
    except Exception as e:
        app.logger.error(f"Error recording payment: {str(e)}")
        flash(f'Error processing payment: {str(e)}', 'danger')
    
    return redirect(url_for('payments_dashboard'))

@app.route('/tenant_payments/<tenant_id>')
@login_required
def tenant_payments(tenant_id):
    """Get payment history for a specific tenant (AJAX endpoint)"""
    try:
        admin_id = session['admin_id']
        
        # Get all payments for this tenant
        payments = list(mongo.db.payments.find({
            'admin_id': ObjectId(admin_id),
            'tenant_id': ObjectId(tenant_id)
        }).sort('created_at', -1))
        
        # Convert ObjectId to string for JSON serialization
        for payment in payments:
            payment['_id'] = str(payment['_id'])
            payment['admin_id'] = str(payment['admin_id'])
            payment['tenant_id'] = str(payment['tenant_id'])
            payment['house_id'] = str(payment['house_id'])
            payment['reading_id'] = str(payment['reading_id'])
            
            # Format dates
            if payment.get('due_date'):
                payment['due_date'] = payment['due_date'].strftime('%Y-%m-%d')
            if payment.get('payment_date'):
                payment['payment_date'] = payment['payment_date'].strftime('%Y-%m-%d')
            if payment.get('created_at'):
                payment['created_at'] = payment['created_at'].strftime('%Y-%m-%d')
        
        return jsonify({'payments': payments})
        
    except Exception as e:
        app.logger.error(f"Error fetching tenant payments: {str(e)}")
        return jsonify({'error': 'Failed to fetch payments'}), 500

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
        readings = list(mongo.db.water_readings.find({
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
    admin_id = ObjectId(session['admin_id'])
    
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
                        "updated_at": datetime.utcnow()
                    }}
                )
            else:
                mongo.db.sms_config.insert_one({
                    "rate_per_unit": rate_per_unit,
                    "updated_at": datetime.utcnow(),
                    "admin_id": admin_id  # Add admin_id
                })
            flash('SMS configuration updated successfully!', 'success')
        except Exception as e:
            flash(f'Error updating SMS configuration: {str(e)}', 'danger')

    return render_template('sms_config.html', 
                          config=config,
                          rate_per_unit=RATE_PER_UNIT,
                          talksasa_api_key=TALKSASA_API_KEY,
                          talksasa_sender_id=TALKSASA_SENDER_ID)

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
                    "created_at": datetime.utcnow()
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
    admin_id = ObjectId(session['admin_id'])
    
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
    admin_id = ObjectId(session['admin_id'])
    
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
                "sent_at": datetime.utcnow()
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
                "created_at": datetime.utcnow()
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
                    "created_at": datetime.utcnow()
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
                        "created_at": datetime.utcnow()
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
                        "date_recorded": datetime.utcnow(),
                        "sms_status": "not_sent"
                    })
                    
                    # Create payment record for this reading
                    month_year = datetime.utcnow().strftime('%Y-%m')
                    payment_data = {
                        'admin_id': admin_id,
                        'tenant_id': tenant_id,
                        'house_id': house_id,
                        'bill_amount': bill_amount,
                        'amount_paid': 0.0,
                        'payment_status': 'unpaid',
                        'due_date': datetime.utcnow() + timedelta(days=30),
                        'month_year': month_year,
                        'reading_id': reading_id,
                        'payment_date': None,
                        'payment_method': None,
                        'notes': '',
                        'created_at': datetime.utcnow(),
                        'updated_at': datetime.utcnow()
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
            mongo.db.water_readings.insert_many(readings_to_insert)
            
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
            "from": "water_readings",
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



"""
# Admin password reset routes
@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        if not username:
            flash('Username is required', 'danger')
            return render_template('forgot_password.html')
        
        admin = mongo.db.admins.find_one({"username": username})
        if admin:
            s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
            token = s.dumps(username, salt='password-reset-salt')
            reset_link = url_for('reset_password', token=token, _external=True)
            flash(f'Reset link (for demo purposes): {reset_link}', 'info')
            app.logger.info(f"Password reset link generated for {username}")
        else:
            flash('No admin with that username found', 'danger')
    return render_template('forgot_password.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    try:
        username = s.loads(token, salt='password-reset-salt', max_age=3600)
    except SignatureExpired:
        flash('The reset link has expired', 'danger')
        return redirect(url_for('forgot_password'))
    except BadSignature:
        flash('Invalid reset link', 'danger')
        return redirect(url_for('forgot_password'))

    admin = mongo.db.admins.find_one({"username": username})
    if not admin:
        flash('Invalid user', 'danger')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')
        if not new_password or not confirm_password:
            flash('All fields are required', 'danger')
            return render_template('reset_password.html', token=token)
        if new_password != confirm_password:
            flash('Passwords do not match', 'danger')
            return render_template('reset_password.html', token=token)
        
        mongo.db.admins.update_one(
            {"username": username},
            {"$set": {"password": generate_password_hash(new_password)}}
        )
        flash('Password has been reset successfully', 'success')
        return redirect(url_for('login'))
    return render_template('reset_password.html', token=token)
"""