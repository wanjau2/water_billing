from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from send_sms import send_message
from flask_migrate import Migrate
import os

load_dotenv()



# import africastalking

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] =  os.environ.get('DATABASE_URL')
app.config['SECRET_KEY'] =os.environ.get('SECRET_KEY')


db = SQLAlchemy(app)
migrate = Migrate(app, db)



class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

class Tenant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    readings = db.relationship('WaterReading', backref='tenant', lazy=True)

class WaterReading(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False)
    previous_reading = db.Column(db.Float, nullable=False)
    current_reading = db.Column(db.Float, nullable=False)
    usage = db.Column(db.Float, nullable=False)
    bill_amount = db.Column(db.Float, nullable=False)
    date_recorded = db.Column(db.DateTime, default=datetime.utcnow)

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        admin = Admin.query.filter_by(username=username).first()
        if admin and check_password_hash(admin.password, password):
            session['admin_id'] = admin.id
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials', 'danger')
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'admin_id' not in session:
        return redirect(url_for('login'))
    
    
    search_query = request.args.get('search', '')
    
   
    if search_query:
        tenants = Tenant.query.filter(Tenant.name.like(f'%{search_query}%')).all()
    else:
        tenants = Tenant.query.all()
    
    readings = db.session.query(WaterReading, Tenant).join(Tenant, WaterReading.tenant_id == Tenant.id).all()
    return render_template('dashboard.html', tenants=tenants, readings=readings, search_query=search_query)

@app.route('/tenant/<int:tenant_id>')
def tenant_details(tenant_id):
    if 'admin_id' not in session:
        return redirect(url_for('login'))
    
    tenant = Tenant.query.get_or_404(tenant_id)
    readings = WaterReading.query.filter_by(tenant_id=tenant_id).order_by(WaterReading.date_recorded).all()
    
    labels = [reading.date_recorded.strftime('%Y-%m-%d') for reading in readings]
    usage_data = [reading.usage for reading in readings]
    bill_data = [reading.bill_amount for reading in readings]
    
    return render_template(
        'tenant_details.html', 
        tenant=tenant, 
        readings=readings,
        labels=labels,
        usage_data=usage_data,
        bill_data=bill_data
    )

@app.route('/api/tenant/<int:tenant_id>/readings')
def tenant_readings_data(tenant_id):
    if 'admin_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    readings = WaterReading.query.filter_by(tenant_id=tenant_id).order_by(WaterReading.date_recorded).all()
    data = []
    for reading in readings:
        data.append({
            'date': reading.date_recorded.strftime('%Y-%m-%d'),
            'previous_reading': reading.previous_reading,
            'current_reading': reading.current_reading,
            'usage': reading.usage,
            'bill_amount': reading.bill_amount
        })
    return jsonify(data)

@app.route('/add_tenant', methods=['POST'])
def add_tenant():
    if 'admin_id' not in session:
        return redirect(url_for('login'))
    
    name = request.form['name']
    phone = request.form['phone']
    
    if not phone.startswith('+'):
        if phone.startswith('0'):
            phone = '+254' + phone[1:] 
        elif not phone.startswith('254'):
            phone = '+254' + phone
    
    existing_tenant = Tenant.query.filter_by(phone=phone).first()
    if existing_tenant:
        flash("Phone number already exists!", "danger")
        return redirect(url_for('dashboard'))
    else:
        new_tenant = Tenant(name=name, phone=phone)
        db.session.add(new_tenant)
        db.session.commit()
        flash('Tenant added successfully!', 'success')
        return redirect(url_for('dashboard'))

@app.route('/record_reading', methods=['POST'])
def record_reading():
    if 'admin_id' not in session:
        return redirect(url_for('login'))
    tenant_id = request.form['tenant_id']
    previous_reading = float(request.form['previous_reading'])
    current_reading = float(request.form['current_reading'])
    usage = current_reading - previous_reading
    # 1 unit = 100 Ksh
    bill_amount = usage * 100  

    new_reading = WaterReading(
        tenant_id=tenant_id,
        previous_reading=previous_reading,
        current_reading=current_reading,
        usage=usage,
        bill_amount=bill_amount
    )
    db.session.add(new_reading)
    db.session.commit()

   
    tenant = Tenant.query.get(tenant_id)
    if tenant:
        message = f"Hello {tenant.name}, your water bill is Ksh {bill_amount}. Thank you!"
        try:
            print(message,tenant.phone)    
            response = send_message(tenant.phone, message)
            app.logger.info("SMS Response: %s", response)
            flash("Water reading recorded and SMS sent!", "success")
        except Exception as e:
            app.logger.error("SMS sending failed: %s", e)
            flash(f"Reading recorded but SMS failed: {str(e)}", "warning")
    else:
        flash("Reading recorded but tenant not found for SMS.", "warning")
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)