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
import pandas as pd
from datetime import datetime
from io import BytesIO
from flask import send_file
from bson import ObjectId

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

        message = f" Hello  {tenant['name']}, your water meter reading is {current_reading} units (used {usage:.2f} units). Your bill is Ksh {bill_amount:.2f}. Thank you!, From Peter Murage"

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

@app.route('/delete_tenant/<tenant_id>', methods=['POST'])
@login_required
def delete_tenant(tenant_id):
    try:
        tenant_id_obj = ObjectId(tenant_id)
        
        # Get tenant details before deletion (for flash message)
        tenant = mongo.db.tenants.find_one({"_id": tenant_id_obj})
        if not tenant:
            flash('Tenant not found', 'danger')
            return redirect(url_for('dashboard'))
            
        tenant_name = tenant.get('name', 'Unknown')
        
        # Delete all water readings for this tenant
        mongo.db.water_readings.delete_many({"tenant_id": tenant_id_obj})
        
        # Delete the tenant
        result = mongo.db.tenants.delete_one({"_id": tenant_id_obj})
        
        if result.deleted_count > 0:
            flash(f'Tenant "{tenant_name}" and all their records have been deleted', 'success')
        else:
            flash('Error deleting tenant', 'danger')
            
    except Exception as e:
        flash(f'Error: {str(e)}', 'danger')
        
    return redirect(url_for('dashboard'))

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

# Excel import route
@app.route('/import_excel', methods=['POST'])
@login_required
def import_excel():
    if 'excel_file' not in request.files:
        flash('No file part', 'danger')
        return redirect(url_for('dashboard'))
    
    file = request.files['excel_file']
    if file.filename == '':
        flash('No selected file', 'danger')
        return redirect(url_for('dashboard'))
    
    if file and (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        try:
            # Read the Excel file
            df = pd.read_excel(file)
            
            # Validate required columns
            required_columns = ['Name', 'Phone', 'Date']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            # Check for reading columns (at least one)
            reading_columns = [col for col in df.columns if 'Reading' in col]
            if not reading_columns:
                missing_columns.append('Reading columns')
            
            if missing_columns:
                flash(f"Missing required columns: {', '.join(missing_columns)}", 'danger')
                return redirect(url_for('dashboard'))
            
            # Process each tenant
            success_count = 0
            error_count = 0
            error_messages = []
            
            # Get rate per unit
            sms_config = mongo.db.sms_config.find_one()
            rate_per_unit = sms_config['rate_per_unit'] if sms_config else 50  # Default rate
            
            # Group by tenant
            for tenant_name, tenant_data in df.groupby('Name'):
                try:
                    # Get or create tenant
                    phone = format_phone_number(str(tenant_data['Phone'].iloc[0]))
                    
                    tenant = mongo.db.tenants.find_one({"name": tenant_name})
                    if not tenant:
                        # Create new tenant
                        tenant_id = mongo.db.tenants.insert_one({
                            "name": tenant_name,
                            "phone": phone
                        }).inserted_id
                    else:
                        tenant_id = tenant['_id']
                    
                    # Sort reading columns to ensure chronological order
                    reading_columns.sort(key=lambda x: int(x.split(' ')[-1]) if x.split(' ')[-1].isdigit() else 0)
                    
                    # Process readings chronologically
                    prev_reading = 0
                    for i, row in tenant_data.iterrows():
                        # Parse date
                        if isinstance(row['Date'], str):
                            try:
                                date_recorded = datetime.strptime(row['Date'], '%Y-%m-%d')
                            except ValueError:
                                date_recorded = datetime.now()
                        elif isinstance(row['Date'], datetime):
                            date_recorded = row['Date']
                        else:
                            date_recorded = datetime.now()
                        
                        # Process each reading column
                        for j, col in enumerate(reading_columns):
                            if pd.notna(row[col]):  # Check if reading is not NaN
                                current_reading = float(row[col])
                                
                                # Calculate usage and bill
                                usage = current_reading - prev_reading if j > 0 else current_reading
                                bill_amount = usage * rate_per_unit
                                
                                # Insert reading if usage is valid
                                if usage >= 0:
                                    mongo.db.water_readings.insert_one({
                                        "tenant_id": ObjectId(tenant_id),
                                        "previous_reading": prev_reading,
                                        "current_reading": current_reading,
                                        "usage": usage,
                                        "bill_amount": bill_amount,
                                        "date_recorded": date_recorded
                                    })
                                    success_count += 1
                                    prev_reading = current_reading
                                else:
                                    error_messages.append(f"Row {i+2}, {col}: Usage cannot be negative")
                                    error_count += 1
                
                except Exception as e:
                    error_messages.append(f"Error processing tenant {tenant_name}: {str(e)}")
                    error_count += 1
            
            # Show results
            if success_count > 0:
                flash(f"Successfully imported {success_count} readings", 'success')
            
            if error_count > 0:
                flash(f"Failed to import {error_count} readings", 'warning')
                for msg in error_messages[:5]:  # Show first 5 errors
                    flash(msg, 'warning')
                if len(error_messages) > 5:
                    flash(f"... and {len(error_messages) - 5} more errors", 'warning')
            
            return redirect(url_for('dashboard'))
            
        except Exception as e:
            flash(f"Error processing file: {str(e)}", 'danger')
            return redirect(url_for('dashboard'))
    else:
        flash('Invalid file format. Please upload an Excel file (.xlsx or .xls)', 'danger')
        return redirect(url_for('dashboard'))

# Route to download Excel template
@app.route('/download_excel_template')
@login_required
def download_excel_template():
    # Retrieve actual data from the database
    tenants = list(mongo.db.tenants.find())
    readings = list(mongo.db.water_readings.find().sort("date_recorded", -1))
    
    # Create a list to store tenant data with readings
    tenant_data = []
    
    # Find the maximum number of readings any tenant has
    max_readings = 0
    tenant_readings_map = {}
    
    # First pass: group readings by tenant and find the maximum
    for tenant in tenants:
        tenant_id = tenant['_id']
        # Get readings for this tenant
        tenant_readings = [r for r in readings if r.get('tenant_id') == tenant_id]
        
        # Sort readings by date (oldest to newest)
        tenant_readings.sort(key=lambda x: x.get('date_recorded', datetime.now()))
        
        # Store readings for this tenant
        tenant_readings_map[tenant_id] = tenant_readings
        
        # Update max readings count
        max_readings = max(max_readings, len(tenant_readings))
    
    # Second pass: create rows with all readings
    for tenant in tenants:
        tenant_id = tenant['_id']
        tenant_name = tenant['name']
        tenant_phone = tenant['phone']
        
        # Get the sorted readings for this tenant
        tenant_readings = tenant_readings_map.get(tenant_id, [])
        
        # Create a base row for this tenant
        row = {
            'Name': tenant_name,
            'Phone': tenant_phone,
        }
        
        # Add all readings with their dates
        for i, reading in enumerate(tenant_readings, 1):
            date_str = reading.get('date_recorded', datetime.now()).strftime('%Y-%m-%d')
            reading_value = reading.get('current_reading', 0)
            
            # Add reading and date to the row
            row[f'Date {i}'] = date_str
            row[f'Reading {i}'] = reading_value
        
        # If tenant has no readings, add at least one empty date/reading field
        if not tenant_readings:
            row['Date 1'] = datetime.now().strftime('%Y-%m-%d')
            row['Reading 1'] = None
        
        tenant_data.append(row)
    
    # If no data exists, add a sample row to show the format
    if not tenant_data:
        sample_row = {
            'Name': 'Example Tenant',
            'Phone': '+254712345678',
            'Date 1': datetime.now().strftime('%Y-%m-%d'),
            'Reading 1': 0,
            'Date 2': (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d'),
            'Reading 2': 10
        }
        tenant_data = [sample_row]
        max_readings = 2
    
    # Create DataFrame from the collected data
    df = pd.DataFrame(tenant_data)
    
    # Ensure all columns are present even if some tenants don't have all readings
    all_columns = ['Name', 'Phone']
    for i in range(1, max_readings + 1):  # Support all readings
        all_columns.extend([f'Date {i}', f'Reading {i}'])
    
    # Add missing columns with None values
    for col in all_columns:
        if col not in df.columns:
            df[col] = None
    
    # Reorder columns to ensure consistent format
    df = df[all_columns]
    
    # Create Excel file in memory
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    
    output.seek(0)
    
    # Send file to user
    return send_file(
        output,
        as_attachment=True,
        download_name='water_billing_template.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

if __name__ == '__main__':
    try:
        create_admin_user()
    except Exception as e:
        app.logger.error(f"Database initialization error: {e}")
        print(f"Database initialization error: {e}")
    
    app.run(debug=True, host='0.0.0.0', port=5000)


