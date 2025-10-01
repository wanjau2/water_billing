# Data Filtering Rules for Multi-Property System

**Date:** 2025-10-01
**Purpose:** Define when to use admin_id-only vs admin_id+property_id filtering

---

## Rule #1: Subscription & Tier Calculations (GLOBAL)

**Filter by:** `admin_id` ONLY
**Reason:** Subscription limits apply to ALL properties combined under one admin

### Examples:
```python
# ✅ CORRECT - Subscription tenant count
total_count = mongo.db.tenants.count_documents({"admin_id": admin_id})

# ✅ CORRECT - Subscription house count
house_count = mongo.db.houses.count_documents({"admin_id": admin_id})

# ✅ CORRECT - Total revenue for billing (if needed)
total_revenue = mongo.db.payments.aggregate([
    {"$match": {"admin_id": admin_id}},
    {"$group": {"_id": None, "total": {"$sum": "$bill_amount"}}}
])
```

### Use Cases:
- `get_total_tenant_count()` - For subscription tier validation
- `check_subscription_limit()` - Checking if admin can add more tenants/houses
- Admin profile statistics showing "Total Tenants: 150" across all properties
- Subscription billing calculations

---

## Rule #2: Data Display & Operations (PROPERTY-SPECIFIC)

**Filter by:** `admin_id` AND `property_id`
**Reason:** Prevent data leakage between properties, show only relevant data

### Examples:
```python
# ✅ CORRECT - Get tenants for current property
current_property_id = get_current_property_id()
tenants = mongo.db.tenants.find({
    "admin_id": admin_id,
    "property_id": current_property_id
})

# ✅ CORRECT - Get readings for current property
readings = mongo.db.meter_readings.find({
    "admin_id": admin_id,
    "property_id": current_property_id
})

# ✅ CORRECT - Generate rent bills for current property only
tenants = mongo.db.tenants.find({
    "admin_id": admin_id,
    "property_id": current_property_id
})
```

### Use Cases:
- Dashboard displays (tenants list, houses list, readings)
- Bill generation (water bills, rent bills)
- Payment dashboards
- Reports and exports
- Tenant management operations
- House management operations
- Any CRUD operations on tenant/house/reading/payment data

---

## Rule #3: Backward Compatibility (Legacy Data)

**Filter by:** `admin_id` AND (`property_id` OR `property_id` doesn't exist)
**Reason:** Handle old data created before multi-property support

### Example:
```python
# ✅ CORRECT - Handle both new and old data
if property_id:
    query = {
        "$and": [
            {"admin_id": admin_id},
            {
                "$or": [
                    {"property_id": property_id},
                    {"property_id": {"$exists": False}}
                ]
            }
        ]
    }
else:
    query = {"admin_id": admin_id}

readings = mongo.db.meter_readings.find(query)
```

### Use Cases:
- Dashboard analytics (showing data from before property_id was added)
- Tenant portal (tenant's historical readings)
- Any query that needs to access legacy data

---

## Rule #4: Cross-Property Operations (ADMIN-SPECIFIC)

**Filter by:** `admin_id` ONLY (with explicit comment)
**Reason:** Some operations intentionally span all properties

### Examples:
```python
# ✅ CORRECT - With explicit comment
# NOTE: Intentionally querying ALL properties for payment reminders
overdue_bills = mongo.db.payments.find({
    "admin_id": admin_id,
    "payment_status": "unpaid"
})

# ✅ CORRECT - Admin settings apply to all properties
admin = mongo.db.admins.find_one({"_id": admin_id})
```

### Use Cases:
- Automated payment reminders (send to all tenants across all properties)
- Global admin settings
- Bulk operations explicitly requested across all properties
- System notifications

---

## Rule #5: M-Pesa/Payment Callbacks (SMART DETECTION)

**Filter by:** `admin_id` + smart property detection
**Reason:** Payment reference might not include property_id, need to detect it

### Strategy:
```python
# 1. Try to find tenant by phone + admin_id
tenants = mongo.db.tenants.find({
    "phone": customer_phone,
    "admin_id": admin_id
})

# 2. If multiple matches (multiple properties), use additional context:
#    - House number in payment reference
#    - Till/Paybill number (if property-specific)
#    - Recent activity
#    - Ask admin to configure property-specific payment numbers

# 3. Store property_id in payment record once determined
```

---

## Quick Reference Table

| Operation | Filter | Reason |
|-----------|--------|--------|
| Subscription limits | `admin_id` only | Combined across all properties |
| Dashboard tenant list | `admin_id` + `property_id` | Show current property only |
| Generate rent bills | `admin_id` + `property_id` | Bill current property only |
| Payment reminders | `admin_id` only (+ validate tenant property) | Send to all properties |
| Analytics totals | `admin_id` only | Show combined stats |
| Analytics breakdown | `admin_id` + `property_id` | Show per-property |
| Tenant CRUD | `admin_id` + `property_id` | Prevent cross-property access |
| M-Pesa callbacks | `admin_id` + detect property | Route payment correctly |

---

## Anti-Patterns (AVOID)

### ❌ WRONG - Missing admin_id
```python
# Could leak data across admins!
tenant = mongo.db.tenants.find_one({"_id": tenant_id})
```

### ❌ WRONG - Property filter on subscription count
```python
# Would give wrong subscription tier!
count = mongo.db.tenants.count_documents({
    "admin_id": admin_id,
    "property_id": property_id  # ❌ WRONG for subscription
})
```

### ❌ WRONG - No property filter on display operations
```python
# Shows tenants from ALL properties!
tenants = mongo.db.tenants.find({"admin_id": admin_id})
return render_template('tenants.html', tenants=tenants)
```

---

## Implementation Checklist

When adding a new database query, ask:

1. ✅ Does this query have `admin_id` filter? (Required for ALL queries)
2. ✅ Is this for subscription/tier calculation? → Use `admin_id` ONLY
3. ✅ Is this for data display/CRUD? → Add `property_id` filter
4. ✅ Does it need to work with legacy data? → Use `$or` pattern
5. ✅ Is it intentionally cross-property? → Add comment explaining why

---

## Migration Strategy

### Phase 1: Add property_id to new records ✅
- All new tenants get property_id
- All new houses get property_id
- All new readings get property_id
- All new payments get property_id

### Phase 2: Fix CRITICAL queries (In Progress)
- Initialize_houses_collection
- send_payment_reminders
- generate_rent_bills
- M-Pesa callbacks

### Phase 3: Fix HIGH RISK queries
- All tenant lookups
- All house lookups
- All reading lookups
- All payment lookups

### Phase 4: Backfill legacy data (Optional)
```python
# Run once to add property_id to existing records
mongo.db.tenants.update_many(
    {"property_id": {"$exists": False}, "admin_id": admin_id},
    {"$set": {"property_id": default_property_id}}
)
```

---

**Last Updated:** 2025-10-01
**Status:** Phase 2 in progress (3/4 CRITICAL fixed)
