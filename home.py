import os
import logging
import urllib.parse
import re
from redis import Redis
from flask_talisman import Talisman
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from dotenv import load_dotenv
import africastalking
from functools import wraps
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Load environment variables from the .env file
load_dotenv()

# Environment variables with fallbacks
AFRICASTALKING_USERNAME = os.environ.get("AFRICASTALKING_USERNAME")
AFRICASTALKING_API_KEY = os.environ.get("AFRICASTALKING_API_KEY")
DEFAULT_SENDER = os.environ.get("AFRICASTALKING_SENDER", "DefaultSender")
SECRET_KEY = os.environ.get("SECRET_KEY", "you-should-change-this")
RATE_PER_UNIT = float(os.environ.get("RATE_PER_UNIT", 100))
DEFAULT_PER_PAGE = int(os.environ.get("DEFAULT_PER_PAGE", 10))

# Azure DB connection parameters (using Azure SQL only)
params = urllib.parse.quote_plus(
    f"Driver={{ODBC Driver 18 for SQL Server}};"
    f"Server={os.environ.get('AZURE_SERVER')};"
    f"Database={os.environ.get('AZURE_DATABASE')};"
    f"Uid={os.environ.get('AZURE_USERNAME')};"
    f"Pwd={os.environ.get('AZURE_PASSWORD')};"
    "Encrypt=yes;"
    "TrustServerCertificate=no;"
    "Connection Timeout=30;"
)

connection_url = URL.create(
    "mssql+pyodbc",
    query={"odbc_connect": params}
)

# Create Flask app
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = connection_url
app.config['SECRET_KEY'] = SECRET_KEY
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize logging to file
handler = logging.FileHandler('app.log')
handler.setLevel(logging.INFO)
app.logger.addHandler(handler)

# Initialize extensions
db = SQLAlchemy(app)
migrate = Migrate(app, db)
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
Talisman(app, content_security_policy=csp)

#limiter = Limiter(key_func=get_remote_address, app=app,storage_uri="redis://localhost:6379")
limiter = Limiter(key_func=get_remote_address, app=app,storage_uri="memory://")


# Initialize AfricasTalking if credentials are available
sms = None
if AFRICASTALKING_USERNAME and AFRICASTALKING_API_KEY:
    try:
        africastalking.initialize(username=AFRICASTALKING_USERNAME, api_key=AFRICASTALKING_API_KEY)
        sms = africastalking.SMS
    except Exception as e:
        app.logger.error(f"Failed to initialize AfricasTalking: {e}")
        sms = None
else:
    app.logger.warning("AfricasTalking credentials not provided. SMS functionality disabled.")


# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            flash('Please log in to access this page', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


# Function to send SMS with retries and proper error handling
def send_message(recipient, message, sender=None, retries=3):
    if not sms:
        return {"error": "SMS service not configured"}

    if sender is None:
        sender = DEFAULT_SENDER

    attempt = 0
    last_error = None
    while attempt < retries:
        try:
            response = sms.send(message, [recipient], sender)
            if "error" not in response:
                return response
        except Exception as e:
            last_error = e
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
    __tablename__ = 'sms_config' 
    id = db.Column(db.Integer, primary_key=True)
    sender_number = db.Column(db.String(50), nullable=False)
    rate_per_unit = db.Column(db.Float, default=RATE_PER_UNIT)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)


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
    per_page = request.args.get('per_page', DEFAULT_PER_PAGE, type=int)
    search_query = request.args.get('search', '').strip()

    tenants_query = Tenant.query
    if search_query:
        tenants_query = tenants_query.filter(Tenant.name.ilike(f'%{search_query}%'))

    tenants_pagination = tenants_query.order_by(Tenant.name).paginate(page=page, per_page=per_page)

    # Get the most recent readings for display
    readings = db.session.query(WaterReading, Tenant).join(
        Tenant, WaterReading.tenant_id == Tenant.id
    ).order_by(
        WaterReading.date_recorded.desc()
    ).limit(20).all()

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
    per_page = request.args.get('per_page', DEFAULT_PER_PAGE, type=int)

    # For table display, use descending order
    readings_pagination = WaterReading.query.filter_by(
        tenant_id=tenant_id
    ).order_by(
        WaterReading.date_recorded.desc()
    ).paginate(page=page, per_page=per_page)

    # For chart data, fetch readings in chronological order
    chart_readings = WaterReading.query.filter_by(tenant_id=tenant_id) \
        .order_by(WaterReading.date_recorded.asc()).all()

    labels = [r.date_recorded.strftime('%Y-%m-%d') for r in chart_readings]
    usage_data = [r.usage for r in chart_readings]
    bill_data = [r.bill_amount for r in chart_readings]

    return render_template(
        'tenant_details.html',
        tenant=tenant,
        readings=readings_pagination.items,
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
        WaterReading.date_recorded.asc()
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

    if not name or not phone:
        flash('Name and phone number are required', 'danger')
        return redirect(url_for('dashboard'))

    try:
        formatted_phone = format_phone_number(phone)
    except ValueError as ve:
        flash(str(ve), 'danger')
        return redirect(url_for('dashboard'))

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

    if not all([tenant_id, previous_reading is not None, current_reading is not None]):
        flash('All fields are required', 'danger')
        return redirect(url_for('dashboard'))

    if previous_reading < 0 or current_reading < 0:
        flash('Readings must be positive numbers', 'danger')
        return redirect(url_for('dashboard'))

    if current_reading < previous_reading:
        flash('Current reading cannot be less than previous reading', 'danger')
        return redirect(url_for('dashboard'))

    tenant = Tenant.query.get(tenant_id)
    if not tenant:
        flash('Tenant not found', 'danger')
        return redirect(url_for('dashboard'))

    sms_config = SMSConfig.query.first()
    rate_per_unit = sms_config.rate_per_unit if sms_config else RATE_PER_UNIT

    usage = current_reading - previous_reading
    bill_amount = usage * rate_per_unit

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

        message = f"Hello {tenant.name}, your water meter reading is {current_reading} units (used {usage:.2f} units). Your bill is Ksh {bill_amount:.2f}. Thank you!"

        if sms:
            sender_number = sms_config.sender_number if sms_config else None
            response = send_message(tenant.phone, message, sender=sender_number)
            if "error" in response:
                new_reading.sms_status = f"failed: {response['error']}"
                flash(f"Reading recorded but SMS failed: {response['error']}", "warning")
            else:
                new_reading.sms_status = "sent"
                flash("Water reading recorded and SMS sent!", "success")
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

        if not sender_number or rate_per_unit is None:
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

    if not name or not phone:
        flash('Name and phone number are required', 'danger')
        return redirect(url_for('tenant_details', tenant_id=tenant_id))

    try:
        formatted_phone = format_phone_number(phone)
    except ValueError as ve:
        flash(str(ve), 'danger')
        return redirect(url_for('tenant_details', tenant_id=tenant_id))

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


# Admin password reset routes
@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        if not username:
            flash('Username is required', 'danger')
            return render_template('forgot_password.html')
        admin = Admin.query.filter_by(username=username).first()
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

    admin = Admin.query.filter_by(username=username).first()
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
        admin.password = generate_password_hash(new_password)
        db.session.commit()
        flash('Password has been reset successfully', 'success')
        return redirect(url_for('login'))
    return render_template('reset_password.html', token=token)


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500


def create_admin_user():
    if Admin.query.count() == 0:
        default_username = os.environ.get('DEFAULT_ADMIN_USERNAME')
        default_password = os.environ.get('DEFAULT_ADMIN_PASSWORD')
        if not default_username or not default_password:
            app.logger.error("Default admin credentials not provided in environment variables.")
            return
        try:
            admin = Admin(
                username=default_username,
                password=generate_password_hash(default_password)
            )
            db.session.add(admin)
            db.session.commit()
            app.logger.info(
                f"Created default admin user: {default_username}. Please change the default credentials immediately.")
            print(f"Created default admin user: {default_username}. Please change the default credentials immediately.")
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Failed to create default admin: {e}")


if __name__ == '__main__':
    with app.app_context():
        try:
            db.create_all()
            create_admin_user()
        except Exception as e:
            app.logger.error(f"Database initialization error: {e}")
            print(f"Database initialization error: {e}")

    # Debug mode is enabled only if FLASK_ENV is not 'production'
    debug_mode = os.environ.get('FLASK_ENV') != 'production'
    app.run(debug=debug_mode, host='0.0.0.0')
