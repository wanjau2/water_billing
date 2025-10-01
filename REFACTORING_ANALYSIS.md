# home.py Refactoring Analysis

**Date:** 2025-10-01
**Current File Size:** 8,907 lines
**Target:** Break into 22 specialized modules
**Expected Main File Size:** ~500-1000 lines

---

## Executive Summary

The `home.py` file has grown to nearly 9,000 lines containing ~80 Flask routes and ~120 functions. This analysis identifies 22 logical modules that can be extracted to improve maintainability, testability, and code organization.

**Key Findings:**
- High-risk circular dependencies exist between payment, billing, and tenant modules
- Core utilities (auth, property management) are used across all modules
- Several self-contained modules can be extracted with zero risk
- Recommended extraction in 6 phases following dependency chain

---

## Current Structure Overview

### File Statistics
- **Total Lines:** 8,907
- **Total Routes:** ~80+
- **Total Functions:** ~120+
- **Decorators:** 5 (login_required, check_subscription_limit, enforce_subscription_payment, etc.)
- **Database Collections Used:** admins, tenants, houses, properties, readings, payments, bills, subscriptions, maintenance_requests, short_urls

### Main Dependencies
- Flask, Flask-PyMongo, Flask-WTF (CSRF)
- MongoDB (PyMongo)
- Custom modules: mpesa_integration, subscription_config, crypto_utils
- External APIs: TalkSasa (SMS), Bitly (URL shortening), M-Pesa

---

## Proposed Module Structure

```
water_billing/
├── home.py                          # Main app entry (500-1000 lines)
├── config/
│   ├── __init__.py
│   └── settings.py                  # Centralized configuration
├── models/
│   ├── __init__.py
│   ├── admin.py
│   ├── tenant.py
│   ├── house.py
│   ├── payment.py
│   └── property.py
├── services/
│   ├── __init__.py
│   ├── auth.py                      # Authentication & session
│   ├── subscription_manager.py      # Subscription logic
│   ├── payment_manager.py           # Payment processing
│   ├── tenant_manager.py            # Tenant CRUD
│   ├── house_manager.py             # House CRUD
│   ├── property_manager.py          # Property management
│   ├── water_billing.py             # Water billing
│   ├── rent_billing.py              # Rent billing
│   ├── garbage_billing.py           # Garbage billing
│   ├── sms_manager.py               # SMS notifications
│   ├── mpesa_handler.py             # M-Pesa integration
│   ├── analytics.py                 # Reports & analytics
│   ├── maintenance.py               # Maintenance requests
│   └── tenant_portal.py             # Tenant self-service
├── utils/
│   ├── __init__.py
│   ├── url_utils.py                 # URL shortening
│   ├── phone_utils.py               # Phone formatting
│   ├── date_utils.py                # Date calculations
│   └── export_import.py             # Data import/export
├── routes/
│   ├── __init__.py
│   ├── api_routes.py                # API endpoints
│   ├── static_routes.py             # Static pages
│   ├── dashboard.py                 # Dashboard routes
│   └── error_handlers.py            # Error handling
└── migrations/
    ├── __init__.py
    └── database_migrations.py       # Schema migrations
```

---

## Detailed Module Breakdown

### Phase 1: Independent Utilities (Extract First - No Dependencies)

#### 1. **error_handlers.py**
**Location:** Lines 3805-3809, 7744-7754, 8819-8875
**Purpose:** Centralized error handling for HTTP errors and exceptions
**Functions:**
- `add_security_headers()` - Add security headers to responses
- `page_not_found()` - 404 handler
- `server_error()` - 500 handler
- `handle_csrf_error()` - CSRF error handler
- `bad_request_error()` - 400 handler
- `handle_exception()` - General exception handler

**Dependencies:** Flask app only
**Circular Import Risk:** ⚪ NONE
**Lines to Extract:** ~60 lines

**Extraction Strategy:**
```python
# routes/error_handlers.py
def register_error_handlers(app):
    @app.errorhandler(404)
    def page_not_found(e):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template('500.html'), 500

    # ... other handlers

# In home.py
from routes.error_handlers import register_error_handlers
register_error_handlers(app)
```

---

#### 2. **static_routes.py**
**Location:** Lines 7723-7742
**Purpose:** Handle static pages (terms, privacy, sitemap, robots.txt)
**Functions:**
- `robots_txt()` - Serve robots.txt
- `sitemap_xml()` - Generate sitemap
- `terms()` - Terms of service page
- `privacy()` - Privacy policy page
- `contact()` - Contact page

**Dependencies:** Flask app only
**Circular Import Risk:** ⚪ NONE
**Lines to Extract:** ~20 lines

**Extraction Strategy:**
```python
# routes/static_routes.py
from flask import Blueprint

static_bp = Blueprint('static_pages', __name__)

@static_bp.route('/robots.txt')
def robots_txt():
    return send_from_directory('static', 'robots.txt')

# In home.py
from routes.static_routes import static_bp
app.register_blueprint(static_bp)
```

---

#### 3. **health_check.py**
**Location:** Lines 8878-8905
**Purpose:** Health monitoring endpoint
**Functions:**
- `health_check()` - Check app and database health

**Dependencies:** Flask app, MongoDB connection check
**Circular Import Risk:** ⚪ NONE
**Lines to Extract:** ~30 lines

---

#### 4. **url_utils.py**
**Location:** Lines 2534-2615, 2959-2982
**Purpose:** URL shortening and custom short URLs
**Functions:**
- `shorten_url_bitly()` - Bitly integration
- `create_custom_short_url()` - Custom short URLs
- `shorten_url()` - Main shortening function
- `redirect_short_url()` - Handle redirects

**Dependencies:** requests, hashlib, MongoDB (for short_urls collection)
**Circular Import Risk:** ⚪ NONE
**Lines to Extract:** ~100 lines

**Extraction Strategy:**
```python
# utils/url_utils.py
class URLShortener:
    def __init__(self, mongo_db, bitly_token=None):
        self.db = mongo_db
        self.bitly_token = bitly_token

    def shorten(self, long_url):
        # Implementation
        pass

# In home.py
from utils.url_utils import URLShortener
url_shortener = URLShortener(mongo.db, BITLY_ACCESS_TOKEN)
```

---

### Phase 2: Core Utilities (Extract Early - Low Risk)

#### 5. **auth.py** ⭐ HIGH PRIORITY
**Location:** Lines 205-219, 3828-4014, 7355-7479, 2309-2314
**Purpose:** Authentication, session management, user profile
**Functions:**
- `login_required` - Decorator for protected routes
- `get_admin_id()` - Get current admin from session
- `login()` - Login route
- `signup()` - Signup route (with subscription)
- `logout()` - Logout route
- `update_profile()` - Profile updates
- `change_password()` - Password changes

**Dependencies:** Flask app, MongoDB, werkzeug.security
**Circular Import Risk:** 🟢 LOW
**Lines to Extract:** ~300 lines

**Why High Priority:**
- `login_required` decorator is used by ~70% of routes
- `get_admin_id()` is called in nearly every function
- Extracting this early prevents circular dependencies

**Extraction Strategy:**
```python
# services/auth.py
from functools import wraps
from flask import session, redirect, url_for

def get_admin_id():
    """Get current admin ID from session"""
    return ObjectId(session.get('admin_id'))

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# In home.py
from services.auth import login_required, get_admin_id
```

---

#### 6. **sms_manager.py**
**Location:** Lines 3043-3126, 6979-7041, 7683-7721, 2082-2294
**Purpose:** SMS notifications and configuration
**Functions:**
- `send_message()` - Send SMS via TalkSasa
- `format_phone_number()` - Kenyan phone format
- `sms_config()` - SMS configuration page
- `test_sms()` - Test SMS sending
- `send_payment_reminders()` - Bulk reminders
- `manual_payment_reminders()` - Manual trigger

**Dependencies:** requests, TALKSASA_API_KEY, MongoDB
**Circular Import Risk:** 🟢 LOW
**Lines to Extract:** ~250 lines

**Extraction Strategy:**
```python
# services/sms_manager.py
class SMSManager:
    def __init__(self, api_key, sender_id, mongo_db):
        self.api_key = api_key
        self.sender_id = sender_id
        self.db = mongo_db

    def send_message(self, phone, message):
        # Implementation
        pass

    def format_phone_number(self, phone):
        # Implementation
        pass

# In home.py
from services.sms_manager import SMSManager
sms_service = SMSManager(TALKSASA_API_KEY, TALKSASA_SENDER_ID, mongo.db)
```

---

#### 7. **migrations.py**
**Location:** Lines 692-936, 811-886, 3813-3821
**Purpose:** Database schema migrations
**Functions:**
- `migrate_existing_readings_to_payments()`
- `migrate_to_meter_readings()`
- `migrate_timestamps()`
- `migrate_collections()`
- `migrate_existing_data()`
- `migrate_payments()`

**Dependencies:** Flask app, MongoDB
**Circular Import Risk:** 🟢 LOW
**Lines to Extract:** ~250 lines

**Note:** These are one-time operations that can be moved to a separate migrations/ directory

---

### Phase 3: Business Logic - Independent Modules

#### 8. **property_manager.py** ⭐ HIGH PRIORITY
**Location:** Lines 558-632, 2316-2757, 2818-2957
**Purpose:** Multi-property support and settings
**Functions:**
- `initialize_properties_collection()`
- `get_current_property_id()` - Get active property from session
- `get_user_properties()` - Get all properties for admin
- `validate_property_access()` - Check property ownership
- `validate_resource_property_access()` - Check resource belongs to property
- `get_property_settings()` - Get property-specific settings
- `get_property_water_rate()` - Get water rate for property
- `get_property_mpesa_config()` - Get M-Pesa config for property
- `get_property_mpesa_api()` - Initialize M-Pesa API for property
- `properties()` - List properties route
- `switch_property()` - Switch active property
- `add_property()` - Create new property
- `edit_property()` - Edit property
- `property_settings()` - Settings page
- `update_property_settings()` - Save settings

**Dependencies:** Flask app, MongoDB, MpesaAPI, decrypt_mpesa_credentials
**Circular Import Risk:** 🟡 MEDIUM
**Lines to Extract:** ~500 lines

**Why High Priority:**
- `get_current_property_id()` is used in almost every route
- Property-level data isolation depends on this
- Many modules need property context

**Extraction Strategy:**
```python
# services/property_manager.py
class PropertyManager:
    def __init__(self, mongo_db):
        self.db = mongo_db

    def get_current_property_id(self):
        return session.get('current_property_id')

    def validate_property_access(self, admin_id, property_id):
        # Implementation
        pass

# In home.py
from services.property_manager import PropertyManager
property_service = PropertyManager(mongo.db)
```

---

#### 9. **garbage_billing.py**
**Location:** Lines 4677-5101
**Purpose:** Garbage collection billing
**Functions:**
- `garbage_utility()` - Garbage billing page
- `generate_garbage_bills()` - Generate monthly bills
- `record_garbage_payment()` - Record payment
- `update_collection_schedule()` - Update schedule
- `update_garbage_bill_statuses()` - Update bill statuses

**Dependencies:** Flask app, MongoDB, get_admin_id, get_current_property_id
**Circular Import Risk:** 🟢 LOW
**Lines to Extract:** ~425 lines

---

#### 10. **maintenance.py**
**Location:** Lines 6682-6831, 4382-4454
**Purpose:** Maintenance request management
**Functions:**
- `maintenance_requests()` - List maintenance requests
- `update_maintenance_request()` - Update request status
- `maintenance_request()` - API endpoint for tenant submissions

**Dependencies:** Flask app, MongoDB, get_admin_id, send_message
**Circular Import Risk:** 🟢 LOW
**Lines to Extract:** ~180 lines

---

#### 11. **analytics.py**
**Location:** Lines 3149-3305, 8668-8817
**Purpose:** Analytics and reporting
**Functions:**
- `calculate_dashboard_analytics()` - Dashboard metrics
- `generate_analytics_report()` - Detailed analytics report

**Dependencies:** Flask app, MongoDB, get_admin_id, get_current_property_id
**Circular Import Risk:** 🟢 LOW
**Lines to Extract:** ~300 lines

---

#### 12. **export_import.py**
**Location:** Lines 7777-8666
**Purpose:** Bulk import/export operations
**Functions:**
- `import_tenants()` - Import tenants from Excel
- `export_data()` - Export data to Excel
- `download_tenant_template()` - Download Excel template
- `bulk_import_readings_excel()` - Import readings from Excel
- `bulk_import_readings_text()` - Import readings from text
- `process_bulk_reading()` - Process bulk readings

**Dependencies:** pandas, xlsxwriter, secure_filename, MongoDB
**Circular Import Risk:** 🟢 LOW
**Lines to Extract:** ~890 lines

---

#### 13. **tenant_portal.py**
**Location:** Lines 1658-1723, 7103-7353
**Purpose:** Tenant self-service portal
**Functions:**
- `generate_tenant_access_token()` - Create secure token
- `verify_tenant_access_token()` - Verify token
- `mark_token_as_used()` - Mark token used
- `tenant_portal()` - Tenant portal page
- `tenant_download_history()` - Download billing history

**Dependencies:** URLSafeTimedSerializer, MongoDB
**Circular Import Risk:** 🟢 LOW
**Lines to Extract:** ~250 lines

---

#### 14. **api_routes.py**
**Location:** Lines 2988-3031
**Purpose:** RESTful API endpoints
**Functions:**
- `api_csrf_token()` - Get CSRF token
- `api_properties()` - Get properties JSON

**Dependencies:** Flask app, MongoDB, get_admin_id
**Circular Import Risk:** 🟢 LOW
**Lines to Extract:** ~50 lines

---

### Phase 4: Core Business Logic (Medium Risk)

#### 15. **payment_manager.py** ⭐ CRITICAL - EXTRACT FIRST IN PHASE 4
**Location:** Lines 3309-3764, 6264-6680
**Purpose:** Payment processing and tracking
**Functions:**
- `create_payment_record()` - Create payment entry
- `get_unpaid_bills()` - Get unpaid bills for tenant
- `get_unpaid_bills_paginated()` - Paginated unpaid bills
- `get_unpaid_bills_with_aggregation()` - Aggregated unpaid bills
- `update_payment_status()` - Update payment status
- `calculate_total_arrears()` - Calculate tenant arrears
- `calculate_late_payment_fine()` - Calculate late fees
- `get_property_billing_settings()` - Get billing config
- `get_all_bills()` - Get all bills
- `get_billing_summary()` - Billing summary
- `payments_dashboard()` - Payments dashboard route
- `unallocated_payments()` - Unallocated payments route
- `allocate_payment()` - Allocate payment to bill
- `record_payment()` - Record manual payment

**Dependencies:** Flask app, MongoDB, get_admin_id, get_current_property_id, get_current_month_year
**Circular Import Risk:** 🔴 HIGH
**Lines to Extract:** ~800 lines

**Why Critical:**
- Water billing depends on `create_payment_record()`
- Rent billing depends on `calculate_total_arrears()`
- M-Pesa handler depends on `update_payment_status()`
- Tenant portal depends on `get_unpaid_bills()`

**Extraction Strategy:**
```python
# services/payment_manager.py
class PaymentManager:
    def __init__(self, mongo_db):
        self.db = mongo_db

    def create_payment_record(self, tenant_id, amount, bill_type, **kwargs):
        # Implementation
        pass

    def calculate_total_arrears(self, tenant_id):
        # Implementation
        pass

# In home.py
from services.payment_manager import PaymentManager
payment_service = PaymentManager(mongo.db)

# Other modules import it
from services.payment_manager import PaymentManager
```

---

#### 16. **subscription_manager.py** ⭐ HIGH PRIORITY
**Location:** Lines 221-511, 513-556, 1943-2081, 1772-1838, 938-1062
**Purpose:** Subscription enforcement and management
**Functions:**
- `initialize_subscription_records()` - Initialize subscriptions
- `check_subscription_limit()` - Decorator for tenant/house limits
- `enforce_subscription_payment()` - Decorator for payment enforcement
- `check_subscription_status_only()` - Check status without enforcement
- `initialize_subscriptions()` - Initialize subscription tiers
- `check_subscription_expiry()` - Check and update expired subscriptions
- `reactivate_subscription_on_payment()` - Reactivate after payment
- `subscription()` - Subscription management page
- `upgrade_subscription()` - Upgrade subscription
- `initiate_subscription_payment()` - Start M-Pesa payment
- `toggle_auto_renew()` - Toggle auto-renewal
- `fix_subscription_dates()` - Fix missing subscription dates
- `test_subscription_enforcement()` - Test enforcement
- `check_payment_status()` - Check payment status

**Dependencies:** Flask app, MongoDB, SUBSCRIPTION_TIERS, get_admin_id, get_total_tenant_count, send_message
**Circular Import Risk:** 🟡 MEDIUM
**Lines to Extract:** ~600 lines

**Extraction Strategy:**
```python
# services/subscription_manager.py
class SubscriptionManager:
    def __init__(self, mongo_db, subscription_tiers):
        self.db = mongo_db
        self.tiers = subscription_tiers

    def check_subscription_limit(self, resource_type='tenant'):
        # Return decorator
        pass

    def enforce_subscription_payment(self, exclude_routes=None):
        # Return decorator
        pass

# In home.py
from services.subscription_manager import SubscriptionManager
subscription_service = SubscriptionManager(mongo.db, SUBSCRIPTION_TIERS)

# Use decorators
@subscription_service.check_subscription_limit('tenant')
def add_tenant():
    pass
```

---

#### 17. **mpesa_handler.py**
**Location:** Lines 1066-1655, 7043-7101
**Purpose:** M-Pesa payment integration
**Functions:**
- `initiate_subscription_payment()` - Start subscription payment
- `mpesa_signup_callback()` - Handle signup payments
- `mpesa_callback()` - Handle subscription payments
- `mpesa_till_callback()` - Handle till payments
- `mpesa_payment_callback()` - Handle water/rent payments
- `mpesa_validation()` - Validate payment requests
- `find_tenant_with_multi_reference()` - Match tenant from payment
- `get_matching_confidence()` - Calculate match confidence
- `get_mpesa_credentials()` - Get credentials for property
- `save_mpesa_config()` - Save M-Pesa config

**Dependencies:** MpesaAPI, MongoDB, format_phone_number, send_message, reactivate_subscription_on_payment
**Circular Import Risk:** 🟡 MEDIUM
**Lines to Extract:** ~600 lines

**Extraction Strategy:**
```python
# services/mpesa_handler.py
class MpesaHandler:
    def __init__(self, mongo_db, sms_service):
        self.db = mongo_db
        self.sms = sms_service

    def handle_payment_callback(self, callback_data):
        # Implementation
        pass

    def find_tenant_with_multi_reference(self, phone, name, amount):
        # Implementation
        pass

# In home.py
from services.mpesa_handler import MpesaHandler
mpesa_service = MpesaHandler(mongo.db, sms_service)
```

---

#### 18. **tenant_manager.py**
**Location:** Lines 4016-4573, 7481-7681, 4244-4380, 5442-5602, 3766-3803
**Purpose:** Tenant CRUD operations
**Functions:**
- `manage_tenants()` - List tenants
- `add_tenant()` - Add new tenant
- `edit_tenant()` - Edit tenant
- `delete_tenant()` - Delete tenant
- `tenant_details()` - View tenant details
- `tenant_readings_data()` - Get tenant readings
- `build_tenant_search_query()` - Build search query
- `transfer_tenant()` - Transfer tenant to another house
- `export_tenant_data()` - Export tenant data

**Dependencies:** Flask app, MongoDB, get_admin_id, get_current_property_id, validate_property_access, format_phone_number, check_subscription_limit
**Circular Import Risk:** 🟢 LOW
**Lines to Extract:** ~600 lines

---

#### 19. **house_manager.py**
**Location:** Lines 5104-5625, 635-689
**Purpose:** House CRUD operations
**Functions:**
- `initialize_houses_collection()` - Initialize houses
- `houses()` - List houses
- `add_house()` - Add new house
- `edit_house()` - Edit house
- `delete_house()` - Delete house
- `assign_tenant()` - Assign tenant to house
- `get_last_house_reading()` - Get last meter reading

**Dependencies:** Flask app, MongoDB, get_admin_id, get_current_property_id, check_subscription_limit, enforce_subscription_payment
**Circular Import Risk:** 🟢 LOW
**Lines to Extract:** ~400 lines

---

### Phase 5: Billing Modules (High Interdependency)

#### 20. **water_billing.py**
**Location:** Lines 4574-6061, 8182-8615
**Purpose:** Water meter billing
**Functions:**
- `water_utility()` - Water billing dashboard
- `record_reading()` - Record house reading
- `record_tenant_reading()` - Record tenant reading
- `bulk_import_readings_excel()` - Bulk import from Excel
- `bulk_import_readings_text()` - Bulk import from text
- `process_bulk_reading()` - Process single reading
- `download_bulk_readings_template()` - Download template
- `get_last_house_reading()` - Get last reading

**Dependencies:** Flask app, MongoDB, get_admin_id, get_current_property_id, get_rate_per_unit, calculate_total_arrears, create_payment_record, send_message, generate_tenant_access_token, shorten_url
**Circular Import Risk:** 🟡 MEDIUM
**Lines to Extract:** ~700 lines

**Key Dependencies:**
- Needs `create_payment_record()` from payment_manager
- Needs `send_message()` from sms_manager
- Needs `generate_tenant_access_token()` from tenant_portal

**Extraction Strategy:**
```python
# services/water_billing.py
class WaterBillingManager:
    def __init__(self, mongo_db, payment_service, sms_service):
        self.db = mongo_db
        self.payment = payment_service
        self.sms = sms_service

    def record_reading(self, house_id, reading, **kwargs):
        # Implementation
        # Use self.payment.create_payment_record()
        pass

# In home.py
from services.water_billing import WaterBillingManager
water_service = WaterBillingManager(mongo.db, payment_service, sms_service)
```

---

#### 21. **rent_billing.py**
**Location:** Lines 6063-6261
**Purpose:** Rent billing
**Functions:**
- `generate_rent_bills()` - Generate monthly rent bills
- `rent_dashboard()` - Rent billing dashboard

**Dependencies:** Flask app, MongoDB, get_admin_id, get_current_month_year, create_payment_record, calculate_total_arrears, send_message, generate_tenant_access_token, shorten_url
**Circular Import Risk:** 🟡 MEDIUM
**Lines to Extract:** ~200 lines

**Key Dependencies:**
- Needs `create_payment_record()` from payment_manager
- Needs `calculate_total_arrears()` from payment_manager
- Needs `send_message()` from sms_manager

---

### Phase 6: Final Integration

#### 22. **dashboard.py**
**Location:** Lines 3823-4242, 2984-2986
**Purpose:** Main dashboard and home routes
**Functions:**
- `index()` - Home page redirect
- `dashboard()` - Main dashboard
- `smartwater()` - Marketing page

**Dependencies:** EVERYTHING - This depends on all other modules
**Circular Import Risk:** 🔴 HIGH
**Lines to Extract:** ~420 lines

**Note:** This should be extracted LAST as it orchestrates all other services

---

## Circular Dependency Solutions

### Strategy 1: Dependency Injection
Instead of importing services in each module, pass them as parameters:

```python
# services/water_billing.py
class WaterBillingManager:
    def __init__(self, db, payment_service, sms_service):
        self.db = db
        self.payment = payment_service
        self.sms = sms_service

# home.py
payment_service = PaymentManager(mongo.db)
sms_service = SMSManager(...)
water_service = WaterBillingManager(mongo.db, payment_service, sms_service)
```

### Strategy 2: Factory Pattern
Create factory functions to initialize services with dependencies:

```python
# services/__init__.py
def create_services(app, mongo_db):
    auth_service = AuthService(mongo_db)
    payment_service = PaymentManager(mongo_db)
    sms_service = SMSManager(mongo_db, app.config['TALKSASA_API_KEY'])
    water_service = WaterBillingManager(mongo_db, payment_service, sms_service)

    return {
        'auth': auth_service,
        'payment': payment_service,
        'sms': sms_service,
        'water': water_service,
    }

# home.py
from services import create_services
services = create_services(app, mongo.db)
```

### Strategy 3: Lazy Imports
Import inside functions when needed (use sparingly):

```python
def process_payment():
    # Import only when function is called
    from services.tenant_manager import get_tenant
    tenant = get_tenant(tenant_id)
```

### Strategy 4: Shared Utils Module
Create a utils module with no business logic dependencies:

```python
# utils/common.py
def format_phone_number(phone):
    """No dependencies on other services"""
    pass

def get_current_month_year():
    """No dependencies on other services"""
    pass
```

### Strategy 5: Use Flask Blueprints
Organize routes into blueprints to avoid circular imports:

```python
# routes/water_routes.py
from flask import Blueprint

water_bp = Blueprint('water', __name__)

@water_bp.route('/water')
def water_utility():
    pass

# home.py
from routes.water_routes import water_bp
app.register_blueprint(water_bp)
```

---

## Recommended Extraction Order

### Week 1: Foundation (No Dependencies)
1. ✅ error_handlers.py
2. ✅ static_routes.py
3. ✅ health_check.py
4. ✅ url_utils.py

**Risk:** ⚪ None
**Expected Issues:** None
**Testing:** Verify error pages, static routes still work

---

### Week 2: Core Utilities (Low Risk)
5. ✅ auth.py ⭐
6. ✅ sms_manager.py
7. ✅ migrations.py (optional - can skip)

**Risk:** 🟢 Low
**Expected Issues:** Import adjustments in many files
**Testing:** Verify login, SMS sending, decorators work

---

### Week 3: Independent Business Logic
8. ✅ property_manager.py ⭐
9. ✅ garbage_billing.py
10. ✅ maintenance.py
11. ✅ analytics.py
12. ✅ export_import.py
13. ✅ tenant_portal.py
14. ✅ api_routes.py

**Risk:** 🟢 Low
**Expected Issues:** Property context needs to be maintained
**Testing:** Test property switching, garbage billing, maintenance requests

---

### Week 4: Core Business Logic (Careful Order Required)
15. ✅ payment_manager.py ⭐⭐⭐ **MUST BE FIRST**
16. ✅ subscription_manager.py ⭐
17. ✅ mpesa_handler.py
18. ✅ tenant_manager.py
19. ✅ house_manager.py

**Risk:** 🟡 Medium to 🔴 High
**Expected Issues:**
- Circular imports between payment and billing
- Decorator adjustments needed
- M-Pesa callbacks need testing

**Testing:**
- Test payment creation
- Test subscription enforcement
- Test M-Pesa callbacks
- Test tenant/house CRUD

---

### Week 5: Billing Modules (High Interdependency)
20. ✅ water_billing.py
21. ✅ rent_billing.py

**Risk:** 🟡 Medium
**Expected Issues:**
- Need payment_manager already extracted
- Need sms_manager already extracted
- Integration testing required

**Testing:**
- Test meter reading recording
- Test bill generation
- Test SMS notifications
- Test payment allocation

---

### Week 6: Final Integration
22. ✅ dashboard.py

**Risk:** 🔴 High
**Expected Issues:**
- Depends on all other modules
- Dashboard analytics need all services
- May need refactoring to use service layer

**Testing:**
- Full integration testing
- Test dashboard loads correctly
- Test all dashboard metrics
- Performance testing

---

## Testing Strategy

### Unit Tests
Create unit tests for each extracted module:

```python
# tests/test_payment_manager.py
import unittest
from services.payment_manager import PaymentManager

class TestPaymentManager(unittest.TestCase):
    def setUp(self):
        # Setup test database
        self.payment_service = PaymentManager(test_db)

    def test_create_payment_record(self):
        # Test payment creation
        pass
```

### Integration Tests
Test module interactions:

```python
# tests/test_water_billing_integration.py
def test_water_billing_creates_payment():
    # Test that water billing correctly creates payment records
    pass
```

### Regression Tests
Ensure existing functionality still works:

```python
# tests/test_regression.py
def test_tenant_can_view_bills():
    # Test complete user flow
    pass
```

---

## Migration Checklist

For each module extraction:

- [ ] Create new module file with proper structure
- [ ] Copy functions to new module
- [ ] Add proper imports and dependencies
- [ ] Update home.py to import from new module
- [ ] Search for function calls across codebase
- [ ] Update all import statements
- [ ] Run application and test routes
- [ ] Run unit tests
- [ ] Run integration tests
- [ ] Update documentation
- [ ] Commit changes with descriptive message
- [ ] Test in staging environment
- [ ] Deploy to production

---

## File Size Impact

| Module | Lines | Percentage |
|--------|-------|------------|
| payment_manager.py | ~800 | 9.0% |
| water_billing.py | ~700 | 7.9% |
| subscription_manager.py | ~600 | 6.7% |
| tenant_manager.py | ~600 | 6.7% |
| mpesa_handler.py | ~600 | 6.7% |
| property_manager.py | ~500 | 5.6% |
| export_import.py | ~890 | 10.0% |
| house_manager.py | ~400 | 4.5% |
| garbage_billing.py | ~425 | 4.8% |
| analytics.py | ~300 | 3.4% |
| auth.py | ~300 | 3.4% |
| sms_manager.py | ~250 | 2.8% |
| tenant_portal.py | ~250 | 2.8% |
| migrations.py | ~250 | 2.8% |
| rent_billing.py | ~200 | 2.2% |
| maintenance.py | ~180 | 2.0% |
| dashboard.py | ~420 | 4.7% |
| url_utils.py | ~100 | 1.1% |
| Other utilities | ~200 | 2.2% |
| **Total Extracted** | **~7,965** | **89.4%** |
| **Remaining in home.py** | **~942** | **10.6%** |

---

## Benefits After Refactoring

### 1. Maintainability
- Each module has single responsibility
- Easier to locate and fix bugs
- Clearer code organization

### 2. Testability
- Unit tests for individual modules
- Mock dependencies easily
- Isolated testing

### 3. Scalability
- Add new features without touching core
- Multiple developers can work on different modules
- Easier to add new billing types

### 4. Reusability
- Payment logic can be reused across billing types
- SMS service can be used anywhere
- Auth decorators available everywhere

### 5. Performance
- Can cache services
- Lazy loading of modules
- Better code splitting

---

## Potential Risks

### 1. Breaking Changes
**Risk:** Routes stop working after extraction
**Mitigation:** Comprehensive testing at each step

### 2. Circular Imports
**Risk:** Services depend on each other
**Mitigation:** Use dependency injection pattern

### 3. Session Management
**Risk:** Property context lost after refactoring
**Mitigation:** Keep session management in one place

### 4. Database Transactions
**Risk:** Multi-step operations may fail partially
**Mitigation:** Add transaction support where needed

### 5. Performance Degradation
**Risk:** Too many imports slow down application
**Mitigation:** Use lazy imports, optimize module structure

---

## Next Steps

1. **Review this analysis** - Verify accuracy and completeness
2. **Prioritize modules** - Decide which to extract first
3. **Setup version control** - Create feature branch for refactoring
4. **Setup testing** - Create test suite before changes
5. **Start with Phase 1** - Extract utilities first
6. **Incremental deployment** - Deploy and test after each phase

---

## Questions to Consider

1. Should we use Flask Blueprints or service classes?
2. Do we want a dependency injection framework or manual DI?
3. Should migrations stay in codebase or separate script?
4. Do we need API versioning for extracted modules?
5. Should we create separate models/ directory for data models?
6. Do we want to add type hints during refactoring?
7. Should we modernize to use dataclasses or Pydantic?
8. Do we want to add comprehensive docstrings?

---

**End of Analysis**
