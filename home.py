import os
import logging
import urllib.parse
import re
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from dotenv import load_dotenv
from functools import wraps
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_pymongo import PyMongo
from bson.objectid import ObjectId
import pymongo.errors

# Load environment variables from the .env file
load_dotenv()

# Environment variables with fallbacks
TALKSASA_API_KEY = os.environ.get("TALKSASA_API_KEY")
TALKSASA_SENDER_ID = os.environ.get("TALKSASA_SENDER_ID", "WATER")
SECRET_KEY = os.environ.get("SECRET_KEY")
RATE_PER_UNIT = float(os.environ.get("RATE_PER_UNIT", 100))
DEFAULT_PER_PAGE = int(os.environ.get("DEFAULT_PER_PAGE", 10))

# MongoDB connection parameters
MONGO_URI = os.environ.get("MONGO_URI")

# Create Flask app
app = Flask(__name__)
app.config['MONGO_URI'] = MONGO_URI
app.config['SECRET_KEY'] = SECRET_KEY

# Initialize logging to file
handler = logging.FileHandler('app.log')
handler.setLevel(logging.INFO)
app.logger.addHandler(handler)

# Initialize extensions
try:
    mongo = PyMongo(app)
    # Test the connection by accessing the database
    mongo.db.command('ping')
    app.logger.info("MongoDB connection successful")
except pymongo.errors.ConnectionFailure as e:
    app.logger.error(f"MongoDB connection failed: {e}")
    print(f"MongoDB connection failed: {e}")
except Exception as e:
    app.logger.error(f"Database initialization error: {e}")
    print(f"Database initialization error: {e}")

csrf = CSRFProtect(app)

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
#Talisman(app, content_security_policy=csp)

limiter = Limiter(key_func=get_remote_address, default_limits=["200 per day", "50 per hour"],app=app,storage_uri="memory://")

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            flash('Please log in to access this page', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function

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
    """Format phone number to international format starting with +254"""
    # Remove spaces, dashes, and parentheses
    phone = re.sub(r'[\s\-\(\)]', '', phone)

    # Validate the phone number contains only digits (optionally starting with +)
    if not re.fullmatch(r'\+?\d+', phone):
        raise ValueError("Invalid phone number format")

    if phone.startswith('+'):
        return phone
    if phone.startswith('0'):
        return '+254' + phone[1:]
    if phone.startswith('254'):
        return '+' + phone
    return '+254' + phone

@app.after_request
def add_security_headers(response):
    # Allow self, cdn.jsdelivr.net for scripts, and add nonce for inline scripts if needed
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data:; style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'"
    return response



# Routes
@app.route('/')
def index():
    return redirect(url_for('login'))

# Rate limiting for login attempts: 5 per minute
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
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

@app.route('/logout')
def logout():
    session.pop('admin_id', None)
    flash('You have been logged out', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', DEFAULT_PER_PAGE, type=int)
    search_query = request.args.get('search', '').strip()

    # Calculate skip value for pagination
    skip = (page - 1) * per_page

    # Build query for MongoDB
    query = {}
    if search_query:
        query["name"] = {"$regex": search_query, "$options": "i"}

    # Get tenants with pagination
    tenants_count = mongo.db.tenants.count_documents(query)
    tenants = list(mongo.db.tenants.find(query).sort("name", 1).skip(skip).limit(per_page))
    
    # Add id attribute to each tenant for template compatibility
    for tenant in tenants:
        tenant['id'] = str(tenant['_id'])
    
    # Create pagination object
    total_pages = (tenants_count + per_page - 1) // per_page
    pagination = {
        'page': page,
        'per_page': per_page,
        'total': tenants_count,
        'pages': total_pages,
        'has_prev': page > 1,
        'has_next': page < total_pages,
        'items': tenants
    }

    # Get the most recent readings for display
    pipeline = [
        {"$lookup": {
            "from": "tenants",
            "localField": "tenant_id",
            "foreignField": "_id",
            "as": "tenant_info"
        }},
        {"$unwind": "$tenant_info"},
        {"$sort": {"date_recorded": -1}},
        {"$limit": 20}
    ]
    readings = list(mongo.db.water_readings.aggregate(pipeline))
    
    # Format readings to match the expected structure
    formatted_readings = []
    for reading in readings:
        # Add id attribute to tenant_info for template compatibility
        reading['tenant_info']['id'] = str(reading['tenant_info']['_id'])
        formatted_readings.append((reading, reading['tenant_info']))

    # Get SMS config
    sms_config = mongo.db.sms_config.find_one()
    rate_per_unit = sms_config['rate_per_unit'] if sms_config else RATE_PER_UNIT

    return render_template(
        'dashboard.html',
        tenants=tenants,
        pagination=pagination,
        readings=formatted_readings,
        search_query=search_query,
        rate_per_unit=rate_per_unit
    )

@app.route('/tenant/<tenant_id>')
@login_required
def tenant_details(tenant_id):
    # Convert string ID to ObjectId
    tenant_id_obj = ObjectId(tenant_id)
    tenant = mongo.db.tenants.find_one({"_id": tenant_id_obj})
    
    if not tenant:
        flash('Tenant not found', 'danger')
        return redirect(url_for('dashboard'))

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', DEFAULT_PER_PAGE, type=int)
    skip = (page - 1) * per_page

    # For table display, use descending order
    readings_count = mongo.db.water_readings.count_documents({"tenant_id": tenant_id_obj})
    readings = list(mongo.db.water_readings.find(
        {"tenant_id": tenant_id_obj}
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
        {"tenant_id": tenant_id_obj}
    ).sort("date_recorded", 1))

    labels = [r['date_recorded'].strftime('%Y-%m-%d') for r in chart_readings]
    usage_data = [r['usage'] for r in chart_readings]
    bill_data = [r['bill_amount'] for r in chart_readings]

    return render_template(
        'tenant_details.html',
        tenant=tenant,
        readings=readings,
        pagination=pagination,
        labels=labels,
        usage_data=usage_data,
        bill_data=bill_data
    )

@app.route('/api/tenant/<tenant_id>/readings')
@login_required
def tenant_readings_data(tenant_id):
    tenant_id_obj = ObjectId(tenant_id)
    tenant = mongo.db.tenants.find_one({"_id": tenant_id_obj})
    
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    readings = list(mongo.db.water_readings.find(
        {"tenant_id": tenant_id_obj}
    ).sort("date_recorded", 1))

    data = []
    for reading in readings:
        data.append({
            'date': reading['date_recorded'].strftime('%Y-%m-%d'),
            'previous_reading': reading['previous_reading'],
            'current_reading': reading['current_reading'],
            'usage': reading['usage'],
            'bill_amount': reading['bill_amount'],
            'sms_status': reading['sms_status']
        })

    return jsonify(data)

@app.route('/add_tenant', methods=['POST'])
@login_required
def add_tenant():
    name = request.form.get('name', '').strip()
    phone = request.form.get('phone', '').strip()

    if not name or not phone:
        flash('Name and phone number are required', 'danger')
        return redirect(url_for('dashboard'))

    try:
        formatted_phone = format_phone_number(phone)
    except ValueError as ve:
        flash(str(ve), 'danger')
        return redirect(url_for('dashboard'))

    existing_tenant = mongo.db.tenants.find_one({"phone": formatted_phone})
    if existing_tenant:
        flash("Phone number already exists!", "danger")
        return redirect(url_for('dashboard'))

    try:
        new_tenant = {
            "name": name,
            "phone": formatted_phone,
            "created_at": datetime.utcnow()
        }
        result = mongo.db.tenants.insert_one(new_tenant)
        flash('Tenant added successfully!', 'success')
    except Exception as e:
        flash(f'Error adding tenant: {str(e)}', 'danger')
        app.logger.error(f"Database error adding tenant: {e}")

    return redirect(url_for('dashboard'))

@app.route('/record_reading', methods=['POST'])
@login_required
def record_reading():
    tenant_id = request.form.get('tenant_id')
    previous_reading = request.form.get('previous_reading', type=float)
    current_reading = request.form.get('current_reading', type=float)

    if not all([tenant_id, previous_reading is not None, current_reading is not None]):
        flash('All fields are required', 'danger')
        return redirect(url_for('dashboard'))

    if previous_reading < 0 or current_reading < 0:
        flash('Readings must be positive numbers', 'danger')
        return redirect(url_for('dashboard'))

    if current_reading < previous_reading:
        flash('Current reading cannot be less than previous reading', 'danger')
        return redirect(url_for('dashboard'))

    tenant_id_obj = ObjectId(tenant_id)
    tenant = mongo.db.tenants.find_one({"_id": tenant_id_obj})
    if not tenant:
        flash('Tenant not found', 'danger')
        return redirect(url_for('dashboard'))

    sms_config = mongo.db.sms_config.find_one()
    rate_per_unit = sms_config['rate_per_unit'] if sms_config else RATE_PER_UNIT

    usage = current_reading - previous_reading
    bill_amount = usage * rate_per_unit

    try:
        new_reading = {
            "tenant_id": tenant_id_obj,
            "previous_reading": previous_reading,
            "current_reading": current_reading,
            "usage": usage,
            "bill_amount": bill_amount,
            "date_recorded": datetime.utcnow(),
            "sms_status": "pending"
        }
        
        result = mongo.db.water_readings.insert_one(new_reading)
        reading_id = result.inserted_id

        message = f"Hello {tenant['name']}, your water meter reading is {current_reading} units (used {usage:.2f} units). Your bill is Ksh {bill_amount:.2f}. Thank you!"

        # Remove the sender_number parameter
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
            flash("Water reading recorded and SMS sent!", "success")

    except Exception as e:
        app.logger.error(f"Error recording reading: {e}")
        flash(f"Error recording reading: {str(e)}", "danger")

    return redirect(url_for('dashboard'))

@app.route('/sms_config', methods=['GET', 'POST'])
@login_required
def sms_config():
    config = mongo.db.sms_config.find_one()

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
                    "updated_at": datetime.utcnow()
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
    tenant_id_obj = ObjectId(tenant_id)
    tenant = mongo.db.tenants.find_one({"_id": tenant_id_obj})
    
    if not tenant:
        flash('Tenant not found', 'danger')
        return redirect(url_for('dashboard'))

    name = request.form.get('name', '').strip()
    phone = request.form.get('phone', '').strip()

    if not name or not phone:
        flash('Name and phone number are required', 'danger')
        return redirect(url_for('tenant_details', tenant_id=tenant_id))

    try:
        formatted_phone = format_phone_number(phone)
    except ValueError as ve:
        flash(str(ve), 'danger')
        return redirect(url_for('tenant_details', tenant_id=tenant_id))

    existing_tenant = mongo.db.tenants.find_one(
        {"phone": formatted_phone, "_id": {"$ne": tenant_id_obj}}
    )
    if existing_tenant:
        flash("Phone number already exists for another tenant!", "danger")
        return redirect(url_for('tenant_details', tenant_id=tenant_id))

    try:
        mongo.db.tenants.update_one(
            {"_id": tenant_id_obj},
            {"$set": {"name": name, "phone": formatted_phone}}
        )
        flash('Tenant updated successfully!', 'success')
    except Exception as e:
        flash(f'Error updating tenant: {str(e)}', 'danger')

    return redirect(url_for('tenant_details', tenant_id=tenant_id))

@app.route('/test_sms', methods=['POST'])
@login_required
def test_sms():
    phone = request.form.get('test_phone', '').strip()
    message = request.form.get('test_message', '').strip()
    
    if not phone or not message:
        flash('Phone number and message are required', 'danger')
        return redirect(url_for('sms_config'))
    
    try:
        formatted_phone = format_phone_number(phone)
        # Remove the sender_number retrieval since it's not used
        
        # Just call send_message without the sender parameter
        response = send_message(formatted_phone, message)
        
        if "error" in response:
            flash(f"Test SMS failed: {response['error']}", "danger")
        else:
            flash("Test SMS sent successfully!", "success")
    except Exception as e:
        flash(f"Error sending test SMS: {str(e)}", "danger")
    
    return redirect(url_for('sms_config'))


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
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500

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

if __name__ == '__main__':
    try:
        create_admin_user()
    except Exception as e:
        app.logger.error(f"Database initialization error: {e}")
        print(f"Database initialization error: {e}")
    
    app.run(debug=True, host='0.0.0.0', port=5000)