import os
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from dotenv import load_dotenv
import africastalking
from functools import wraps
from flask_wtf.csrf import CSRFProtect

# Load environment variables from the .env file
load_dotenv()

# Get environment variables with fallbacks
AFRICASTALKING_USERNAME = os.environ.get("AFRICASTALKING_USERNAME")
AFRICASTALKING_API_KEY = os.environ.get("AFRICASTALKING_API_KEY")
DEFAULT_SENDER = os.environ.get("AFRICASTALKING_SENDER")
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///water_billing.db")
SECRET_KEY = os.environ.get("SECRET_KEY", os.urandom(24).hex())
RATE_PER_UNIT = float(os.environ.get("RATE_PER_UNIT", 100))

# Validate critical environment variables
if not all([AFRICASTALKING_USERNAME, AFRICASTALKING_API_KEY]):
    print("WARNING: AfricasTalking credentials missing. SMS functionality will not work.")

# Create Flask app
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SECRET_KEY'] = SECRET_KEY
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db = SQLAlchemy(app)
migrate = Migrate(app, db)
csrf = CSRFProtect(app)

# Initialize AfricasTalking if credentials are available
sms = None
if AFRICASTALKING_USERNAME and AFRICASTALKING_API_KEY:
    try:
        africastalking.initialize(username=AFRICASTALKING_USERNAME, api_key=AFRICASTALKING_API_KEY)
        sms = africastalking.SMS
    except Exception as e:
        print(f"Failed to initialize AfricasTalking: {e}")

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            flash('Please log in to access this page', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Function to send SMS
def send_message(recipient, message, sender=None):
    if not sms:
        return {"error": "SMS service not configured"}
    
    if sender is None:
        sender = DEFAULT_SENDER
    
    try:
        response = sms.send(message, [recipient], sender)
        return response
    except Exception as e:
        app.logger.error(f"Error sending message: {e}")
        return {"error": str(e)}

# Phone number formatter
def format_phone_number(phone):
    """Format phone number to international format starting with +254"""
    # Remove any spaces, dashes, or parentheses
    phone = ''.join(c for c in phone if c.isdigit() or c == '+')
    
    # If number already has international format, return it
    if phone.startswith('+'):
        return phone
    
    # If number starts with 0, replace it with +254
    if phone.startswith('0'):
        return '+254' + phone[1:]
    
    # If number starts with 254, add +
    if phone.startswith('254'):
        return '+' + phone
    
    # Otherwise, assume it's a local number and add +254
    return '+254' + phone

# Models
class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Tenant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    readings = db.relationship('WaterReading', backref='tenant', lazy=True)

class WaterReading(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    previous_reading = db.Column(db.Float, nullable=False)
    current_reading = db.Column(db.Float, nullable=False)
    usage = db.Column(db.Float, nullable=False)
    bill_amount = db.Column(db.Float, nullable=False)
    date_recorded = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    sms_status = db.Column(db.String(50), nullable=True)

class SMSConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_number = db.Column(db.String(50), nullable=False)
    rate_per_unit = db.Column(db.Float, default=RATE_PER_UNIT)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

# Routes
@app.route('/')
def index():
    return redirect(url_for('login'))

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
            
        admin = Admin.query.filter_by(username=username).first()
        if admin and check_password_hash(admin.password, password):
            session['admin_id'] = admin.id
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
    per_page = request.args.get('per_page', 10, type=int)
    search_query = request.args.get('search', '').strip()
    
    # Query for tenants with pagination
    tenants_query = Tenant.query
    if search_query:
        tenants_query = tenants_query.filter(Tenant.name.ilike(f'%{search_query}%'))
    
    tenants_pagination = tenants_query.order_by(Tenant.name).paginate(page=page, per_page=per_page)
    
    # Get the most recent readings for display
    readings = db.session.query(
        WaterReading, Tenant
    ).join(
        Tenant, WaterReading.tenant_id == Tenant.id
    ).order_by(
        WaterReading.date_recorded.desc()
    ).limit(20).all()
    
    # Get SMS config for display
    sms_config = SMSConfig.query.first()
    rate_per_unit = sms_config.rate_per_unit if sms_config else RATE_PER_UNIT
    
    return render_template(
        'dashboard.html', 
        tenants=tenants_pagination.items,
        pagination=tenants_pagination,
        readings=readings, 
        search_query=search_query,
        rate_per_unit=rate_per_unit
    )

@app.route('/tenant/<int:tenant_id>')
@login_required
def tenant_details(tenant_id):
    tenant = Tenant.query.get_or_404(tenant_id)
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    
    readings_pagination = WaterReading.query.filter_by(
        tenant_id=tenant_id
    ).order_by(
        WaterReading.date_recorded.desc()
    ).paginate(page=page, per_page=per_page)
    
    # Prepare data for charts
    readings = readings_pagination.items
    labels = [reading.date_recorded.strftime('%Y-%m-%d') for reading in readings]
    usage_data = [reading.usage for reading in readings]
    bill_data = [reading.bill_amount for reading in readings]
    
    # Reverse for chronological order in charts
    labels.reverse()
    usage_data.reverse()
    bill_data.reverse()
    
    return render_template(
        'tenant_details.html', 
        tenant=tenant, 
        readings=readings,
        pagination=readings_pagination,
        labels=labels, 
        usage_data=usage_data, 
        bill_data=bill_data
    )

@app.route('/api/tenant/<int:tenant_id>/readings')
@login_required
def tenant_readings_data(tenant_id):
    tenant = Tenant.query.get_or_404(tenant_id)
    
    readings = WaterReading.query.filter_by(
        tenant_id=tenant_id
    ).order_by(
        WaterReading.date_recorded
    ).all()
    
    data = []
    for reading in readings:
        data.append({
            'date': reading.date_recorded.strftime('%Y-%m-%d'),
            'previous_reading': reading.previous_reading,
            'current_reading': reading.current_reading,
            'usage': reading.usage,
            'bill_amount': reading.bill_amount,
            'sms_status': reading.sms_status
        })
    
    return jsonify(data)

@app.route('/add_tenant', methods=['POST'])
@login_required
def add_tenant():
    name = request.form.get('name', '').strip()
    phone = request.form.get('phone', '').strip()
    
    # Validate input
    if not name or not phone:
        flash('Name and phone number are required', 'danger')
        return redirect(url_for('dashboard'))
    
    # Format phone number
    formatted_phone = format_phone_number(phone)
    
    # Check if phone number already exists
    existing_tenant = Tenant.query.filter_by(phone=formatted_phone).first()
    if existing_tenant:
        flash("Phone number already exists!", "danger")
        return redirect(url_for('dashboard'))
    
    try:
        new_tenant = Tenant(name=name, phone=formatted_phone)
        db.session.add(new_tenant)
        db.session.commit()
        flash('Tenant added successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding tenant: {str(e)}', 'danger')
        app.logger.error(f"Database error adding tenant: {e}")
    
    return redirect(url_for('dashboard'))

@app.route('/record_reading', methods=['POST'])
@login_required
def record_reading():
    tenant_id = request.form.get('tenant_id', type=int)
    previous_reading = request.form.get('previous_reading', type=float)
    current_reading = request.form.get('current_reading', type=float)
    
    # Validate input
    if not all([tenant_id, previous_reading is not None, current_reading is not None]):
        flash('All fields are required', 'danger')
        return redirect(url_for('dashboard'))
    
    # Validate readings
    if current_reading < previous_reading:
        flash('Current reading cannot be less than previous reading', 'danger')
        return redirect(url_for('dashboard'))
    
    # Get tenant
    tenant = Tenant.query.get(tenant_id)
    if not tenant:
        flash('Tenant not found', 'danger')
        return redirect(url_for('dashboard'))
    
    # Get billing rate
    sms_config = SMSConfig.query.first()
    rate_per_unit = sms_config.rate_per_unit if sms_config else RATE_PER_UNIT
    
    # Calculate usage and bill
    usage = current_reading - previous_reading
    bill_amount = usage * rate_per_unit
    
    # Create new reading record
    try:
        new_reading = WaterReading(
            tenant_id=tenant_id,
            previous_reading=previous_reading,
            current_reading=current_reading,
            usage=usage,
            bill_amount=bill_amount,
            sms_status="pending"
        )
        db.session.add(new_reading)
        db.session.commit()
        
        # Send SMS notification
        message = f"Hello {tenant.name}, your water meter reading is {current_reading} units (used {usage:.2f} units). Your bill is Ksh {bill_amount:.2f}. Thank you!"
        
        if sms:
            sender_number = sms_config.sender_number if sms_config else None
            try:
                response = send_message(tenant.phone, message, sender=sender_number)
                if "error" in response:
                    new_reading.sms_status = f"failed: {response['error']}"
                    flash(f"Reading recorded but SMS failed: {response['error']}", "warning")
                else:
                    new_reading.sms_status = "sent"
                    flash("Water reading recorded and SMS sent!", "success")
            except Exception as e:
                new_reading.sms_status = f"failed: {str(e)}"
                app.logger.error(f"SMS sending failed: {e}")
                flash(f"Reading recorded but SMS failed: {str(e)}", "warning")
            
            db.session.commit()
        else:
            new_reading.sms_status = "not configured"
            db.session.commit()
            flash("Reading recorded. SMS not sent (service not configured).", "info")
    
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error recording reading: {e}")
        flash(f"Error recording reading: {str(e)}", "danger")
    
    return redirect(url_for('dashboard'))

@app.route('/sms_config', methods=['GET', 'POST'])
@login_required
def sms_config():
    config = SMSConfig.query.first()
    
    if request.method == 'POST':
        sender_number = request.form.get('sender_number', '').strip()
        rate_per_unit = request.form.get('rate_per_unit', type=float)
        
        if not sender_number or not rate_per_unit:
            flash('All fields are required', 'danger')
            return redirect(url_for('sms_config'))
        
        try:
            if config:
                config.sender_number = sender_number
                config.rate_per_unit = rate_per_unit
                config.updated_at = datetime.utcnow()
            else:
                config = SMSConfig(sender_number=sender_number, rate_per_unit=rate_per_unit)
                db.session.add(config)
            
            db.session.commit()
            flash('SMS configuration updated successfully!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating SMS configuration: {str(e)}', 'danger')
    
    return render_template('sms_config.html', config=config, rate_per_unit=RATE_PER_UNIT)

@app.route('/edit_tenant/<int:tenant_id>', methods=['POST'])
@login_required
def edit_tenant(tenant_id):
    tenant = Tenant.query.get_or_404(tenant_id)
    
    name = request.form.get('name', '').strip()
    phone = request.form.get('phone', '').strip()
    
    # Validate input
    if not name or not phone:
        flash('Name and phone number are required', 'danger')
        return redirect(url_for('tenant_details', tenant_id=tenant_id))
    
    # Format phone number
    formatted_phone = format_phone_number(phone)
    
    # Check if phone number already exists for a different tenant
    existing_tenant = Tenant.query.filter(Tenant.phone == formatted_phone, Tenant.id != tenant_id).first()
    if existing_tenant:
        flash("Phone number already exists for another tenant!", "danger")
        return redirect(url_for('tenant_details', tenant_id=tenant_id))
    
    try:
        tenant.name = name
        tenant.phone = formatted_phone
        db.session.commit()
        flash('Tenant updated successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating tenant: {str(e)}', 'danger')
    
    return redirect(url_for('tenant_details', tenant_id=tenant_id))

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500

# Function to create an admin user if none exists
def create_admin_user():
    if Admin.query.count() == 0:
        default_username = os.environ.get('DEFAULT_ADMIN_USERNAME', 'admin')
        default_password = os.environ.get('DEFAULT_ADMIN_PASSWORD', 'admin123')
        
        try:
            admin = Admin(
                username=default_username,
                password=generate_password_hash(default_password)
            )
            db.session.add(admin)
            db.session.commit()
            app.logger.info(f"Created default admin user: {default_username}")
            print(f"Created default admin user: {default_username}")
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Failed to create default admin: {e}")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        create_admin_user()
    
    # Use debug mode only in development
    debug_mode = os.environ.get('FLASK_ENV') == 'development'
    app.run(debug=debug_mode, host='0.0.0.0')
