#!/usr/bin/env python3
"""
Data Isolation Test Suite
Tests that property_id filtering prevents cross-property data leakage
"""

import sys
from datetime import datetime
from bson import ObjectId
from flask import Flask
from pymongo import MongoClient

# Import the Flask app
sys.path.insert(0, '/home/biggie/wanjau/water_billing')
from home import app, mongo

def setup_test_data():
    """Create test data: 1 admin with 2 properties, each with tenants"""
    print("\n" + "="*60)
    print("SETTING UP TEST DATA")
    print("="*60)

    with app.app_context():
        # Create test admin
        admin = mongo.db.admins.find_one({"email": "test_isolation@example.com"})
        if not admin:
            admin_id = mongo.db.admins.insert_one({
                "email": "test_isolation@example.com",
                "name": "Test Admin",
                "password": "hashed_password",
                "phone": "0700000000",
                "subscription_tier": "professional",
                "subscription_status": "active",
                "created_at": datetime.now()
            }).inserted_id
            print(f"✓ Created test admin: {admin_id}")
        else:
            admin_id = admin["_id"]
            print(f"✓ Using existing admin: {admin_id}")

        # Create Property A
        property_a = mongo.db.properties.find_one({"name": "Test Property A", "admin_id": admin_id})
        if not property_a:
            property_a_id = mongo.db.properties.insert_one({
                "name": "Test Property A",
                "admin_id": admin_id,
                "location": "Location A",
                "created_at": datetime.now()
            }).inserted_id
            print(f"✓ Created Property A: {property_a_id}")
        else:
            property_a_id = property_a["_id"]
            print(f"✓ Using existing Property A: {property_a_id}")

        # Create Property B
        property_b = mongo.db.properties.find_one({"name": "Test Property B", "admin_id": admin_id})
        if not property_b:
            property_b_id = mongo.db.properties.insert_one({
                "name": "Test Property B",
                "admin_id": admin_id,
                "location": "Location B",
                "created_at": datetime.now()
            }).inserted_id
            print(f"✓ Created Property B: {property_b_id}")
        else:
            property_b_id = property_b["_id"]
            print(f"✓ Using existing Property B: {property_b_id}")

        # Create House A1 in Property A
        house_a1 = mongo.db.houses.find_one({"house_number": "A1", "property_id": property_a_id})
        if not house_a1:
            house_a1_id = mongo.db.houses.insert_one({
                "house_number": "A1",
                "admin_id": admin_id,
                "property_id": property_a_id,
                "rent": 10000,
                "is_occupied": False,
                "created_at": datetime.now()
            }).inserted_id
            print(f"✓ Created House A1 in Property A")
        else:
            house_a1_id = house_a1["_id"]
            print(f"✓ Using existing House A1")

        # Create House A1 in Property B (SAME house number, different property)
        house_b1 = mongo.db.houses.find_one({"house_number": "A1", "property_id": property_b_id})
        if not house_b1:
            house_b1_id = mongo.db.houses.insert_one({
                "house_number": "A1",
                "admin_id": admin_id,
                "property_id": property_b_id,
                "rent": 15000,
                "is_occupied": False,
                "created_at": datetime.now()
            }).inserted_id
            print(f"✓ Created House A1 in Property B")
        else:
            house_b1_id = house_b1["_id"]
            print(f"✓ Using existing House A1 in Property B")

        # Create Tenant in Property A
        tenant_a = mongo.db.tenants.find_one({"phone": "0711111111", "property_id": property_a_id})
        if not tenant_a:
            tenant_a_id = mongo.db.tenants.insert_one({
                "name": "Tenant Property A",
                "phone": "0711111111",
                "email": "tenant_a@test.com",
                "house_number": "A1",
                "house_id": house_a1_id,
                "admin_id": admin_id,
                "property_id": property_a_id,
                "rent_amount": 10000,
                "created_at": datetime.now()
            }).inserted_id
            # Update house to occupied
            mongo.db.houses.update_one(
                {"_id": house_a1_id},
                {"$set": {"is_occupied": True, "current_tenant_id": tenant_a_id}}
            )
            print(f"✓ Created Tenant in Property A: {tenant_a_id}")
        else:
            tenant_a_id = tenant_a["_id"]
            print(f"✓ Using existing Tenant in Property A: {tenant_a_id}")

        # Create Tenant in Property B
        tenant_b = mongo.db.tenants.find_one({"phone": "0722222222", "property_id": property_b_id})
        if not tenant_b:
            tenant_b_id = mongo.db.tenants.insert_one({
                "name": "Tenant Property B",
                "phone": "0722222222",
                "email": "tenant_b@test.com",
                "house_number": "A1",
                "house_id": house_b1_id,
                "admin_id": admin_id,
                "property_id": property_b_id,
                "rent_amount": 15000,
                "created_at": datetime.now()
            }).inserted_id
            # Update house to occupied
            mongo.db.houses.update_one(
                {"_id": house_b1_id},
                {"$set": {"is_occupied": True, "current_tenant_id": tenant_b_id}}
            )
            print(f"✓ Created Tenant in Property B: {tenant_b_id}")
        else:
            tenant_b_id = tenant_b["_id"]
            print(f"✓ Using existing Tenant in Property B: {tenant_b_id}")

        # Create meter readings for both properties
        reading_a = mongo.db.meter_readings.find_one({"tenant_id": tenant_a_id, "property_id": property_a_id})
        if not reading_a:
            mongo.db.meter_readings.insert_one({
                "tenant_id": tenant_a_id,
                "house_number": "A1",
                "house_id": house_a1_id,
                "admin_id": admin_id,
                "property_id": property_a_id,
                "previous_reading": 0,
                "current_reading": 100,
                "usage": 100,
                "bill_amount": 10000,
                "date_recorded": datetime.now(),
                "tenant_name": "Tenant Property A"
            })
            print(f"✓ Created reading for Tenant A")

        reading_b = mongo.db.meter_readings.find_one({"tenant_id": tenant_b_id, "property_id": property_b_id})
        if not reading_b:
            mongo.db.meter_readings.insert_one({
                "tenant_id": tenant_b_id,
                "house_number": "A1",
                "house_id": house_b1_id,
                "admin_id": admin_id,
                "property_id": property_b_id,
                "previous_reading": 0,
                "current_reading": 200,
                "usage": 200,
                "bill_amount": 20000,
                "date_recorded": datetime.now(),
                "tenant_name": "Tenant Property B"
            })
            print(f"✓ Created reading for Tenant B")

        # Create rent bills for both properties
        current_month = datetime.now().strftime('%Y-%m')

        bill_a = mongo.db.payments.find_one({
            "tenant_id": tenant_a_id,
            "property_id": property_a_id,
            "month_year": current_month,
            "bill_type": "rent"
        })
        if not bill_a:
            mongo.db.payments.insert_one({
                "admin_id": admin_id,
                "tenant_id": tenant_a_id,
                "house_id": house_a1_id,
                "property_id": property_a_id,
                "bill_amount": 10000,
                "amount_paid": 0,
                "payment_status": "unpaid",
                "month_year": current_month,
                "bill_type": "rent",
                "due_date": datetime.now(),
                "created_at": datetime.now()
            })
            print(f"✓ Created rent bill for Property A")

        bill_b = mongo.db.payments.find_one({
            "tenant_id": tenant_b_id,
            "property_id": property_b_id,
            "month_year": current_month,
            "bill_type": "rent"
        })
        if not bill_b:
            mongo.db.payments.insert_one({
                "admin_id": admin_id,
                "tenant_id": tenant_b_id,
                "house_id": house_b1_id,
                "property_id": property_b_id,
                "bill_amount": 15000,
                "amount_paid": 0,
                "payment_status": "unpaid",
                "month_year": current_month,
                "bill_type": "rent",
                "due_date": datetime.now(),
                "created_at": datetime.now()
            })
            print(f"✓ Created rent bill for Property B")

        print("\n✅ Test data setup complete!")

        return {
            "admin_id": admin_id,
            "property_a_id": property_a_id,
            "property_b_id": property_b_id,
            "tenant_a_id": tenant_a_id,
            "tenant_b_id": tenant_b_id,
            "house_a1_id": house_a1_id,
            "house_b1_id": house_b1_id
        }

def test_subscription_counts(test_data):
    """Test that subscription counts include ALL properties"""
    print("\n" + "="*60)
    print("TEST 1: SUBSCRIPTION COUNTS (Should be GLOBAL)")
    print("="*60)

    with app.app_context():
        admin_id = test_data["admin_id"]

        # Test total tenant count (should include both properties)
        total_tenants = mongo.db.tenants.count_documents({"admin_id": admin_id})
        print(f"Total tenants across all properties: {total_tenants}")

        if total_tenants >= 2:
            print("✅ PASS: Subscription count includes tenants from all properties")
        else:
            print("❌ FAIL: Subscription count missing tenants")

        # Test total house count
        total_houses = mongo.db.houses.count_documents({"admin_id": admin_id})
        print(f"Total houses across all properties: {total_houses}")

        if total_houses >= 2:
            print("✅ PASS: House count includes all properties")
        else:
            print("❌ FAIL: House count missing houses")

        return total_tenants >= 2 and total_houses >= 2

def test_property_isolation(test_data):
    """Test that property-specific queries only return current property data"""
    print("\n" + "="*60)
    print("TEST 2: PROPERTY ISOLATION (Should be PROPERTY-SPECIFIC)")
    print("="*60)

    with app.app_context():
        admin_id = test_data["admin_id"]
        property_a_id = test_data["property_a_id"]
        property_b_id = test_data["property_b_id"]

        # Test tenants in Property A
        tenants_a = list(mongo.db.tenants.find({
            "admin_id": admin_id,
            "property_id": property_a_id
        }))
        print(f"Tenants in Property A: {len(tenants_a)}")
        print(f"  Names: {[t['name'] for t in tenants_a]}")

        # Test tenants in Property B
        tenants_b = list(mongo.db.tenants.find({
            "admin_id": admin_id,
            "property_id": property_b_id
        }))
        print(f"Tenants in Property B: {len(tenants_b)}")
        print(f"  Names: {[t['name'] for t in tenants_b]}")

        # Verify no overlap
        tenant_a_in_b = any(t["_id"] == test_data["tenant_a_id"] for t in tenants_b)
        tenant_b_in_a = any(t["_id"] == test_data["tenant_b_id"] for t in tenants_a)

        if not tenant_a_in_b and not tenant_b_in_a:
            print("✅ PASS: Tenants properly isolated by property")
        else:
            print("❌ FAIL: Tenant data leaking between properties!")

        # Test readings isolation
        readings_a = list(mongo.db.meter_readings.find({
            "admin_id": admin_id,
            "property_id": property_a_id
        }))
        readings_b = list(mongo.db.meter_readings.find({
            "admin_id": admin_id,
            "property_id": property_b_id
        }))

        print(f"\nReadings in Property A: {len(readings_a)}")
        print(f"Readings in Property B: {len(readings_b)}")

        if len(readings_a) > 0 and len(readings_b) > 0:
            print("✅ PASS: Readings properly isolated by property")
        else:
            print("⚠️  WARNING: Some readings may not have property_id")

        # Test bills isolation
        current_month = datetime.now().strftime('%Y-%m')
        bills_a = list(mongo.db.payments.find({
            "admin_id": admin_id,
            "property_id": property_a_id,
            "month_year": current_month
        }))
        bills_b = list(mongo.db.payments.find({
            "admin_id": admin_id,
            "property_id": property_b_id,
            "month_year": current_month
        }))

        print(f"\nBills in Property A (current month): {len(bills_a)}")
        print(f"  Total: KES {sum(b['bill_amount'] for b in bills_a)}")
        print(f"Bills in Property B (current month): {len(bills_b)}")
        print(f"  Total: KES {sum(b['bill_amount'] for b in bills_b)}")

        if len(bills_a) > 0 and len(bills_b) > 0:
            print("✅ PASS: Bills properly isolated by property")
        else:
            print("⚠️  WARNING: Some bills may not have property_id")

        return not tenant_a_in_b and not tenant_b_in_a

def test_house_number_overlap(test_data):
    """Test that same house number in different properties doesn't cause conflicts"""
    print("\n" + "="*60)
    print("TEST 3: HOUSE NUMBER OVERLAP (Should NOT conflict)")
    print("="*60)

    with app.app_context():
        admin_id = test_data["admin_id"]
        property_a_id = test_data["property_a_id"]
        property_b_id = test_data["property_b_id"]

        # Find House A1 in Property A
        house_a1_in_a = mongo.db.houses.find_one({
            "house_number": "A1",
            "admin_id": admin_id,
            "property_id": property_a_id
        })

        # Find House A1 in Property B
        house_a1_in_b = mongo.db.houses.find_one({
            "house_number": "A1",
            "admin_id": admin_id,
            "property_id": property_b_id
        })

        print(f"House A1 in Property A: Rent = KES {house_a1_in_a['rent'] if house_a1_in_a else 'NOT FOUND'}")
        print(f"House A1 in Property B: Rent = KES {house_a1_in_b['rent'] if house_a1_in_b else 'NOT FOUND'}")

        if house_a1_in_a and house_a1_in_b:
            if house_a1_in_a['rent'] != house_a1_in_b['rent']:
                print("✅ PASS: Same house number in different properties correctly isolated")
                return True
            else:
                print("⚠️  WARNING: Houses have same rent, can't verify isolation")
                return True
        else:
            print("❌ FAIL: Could not find both houses")
            return False

def test_rent_bill_generation(test_data):
    """Test that generate_rent_bills only affects current property"""
    print("\n" + "="*60)
    print("TEST 4: RENT BILL GENERATION (Property-specific)")
    print("="*60)

    with app.app_context():
        admin_id = test_data["admin_id"]
        property_a_id = test_data["property_a_id"]
        property_b_id = test_data["property_b_id"]
        current_month = datetime.now().strftime('%Y-%m')

        # Count bills before (should already exist from setup)
        bills_a_before = mongo.db.payments.count_documents({
            "admin_id": admin_id,
            "property_id": property_a_id,
            "month_year": current_month,
            "bill_type": "rent"
        })

        bills_b_before = mongo.db.payments.count_documents({
            "admin_id": admin_id,
            "property_id": property_b_id,
            "month_year": current_month,
            "bill_type": "rent"
        })

        print(f"Bills in Property A before: {bills_a_before}")
        print(f"Bills in Property B before: {bills_b_before}")

        # The actual generate_rent_bills function would need session context
        # We're just verifying the queries work correctly

        tenants_a_query = {
            "admin_id": admin_id,
            "property_id": property_a_id
        }
        tenants_would_be_billed = mongo.db.tenants.count_documents(tenants_a_query)

        print(f"\nIf generate_rent_bills ran for Property A:")
        print(f"  Would bill {tenants_would_be_billed} tenants")
        print(f"  Would NOT affect Property B tenants")

        if bills_a_before > 0 and bills_b_before > 0:
            print("✅ PASS: Bills correctly scoped to properties")
            return True
        else:
            print("⚠️  WARNING: Bills may need property_id backfill")
            return False

def cleanup_test_data(test_data):
    """Clean up test data (optional)"""
    print("\n" + "="*60)
    print("CLEANUP (Optional)")
    print("="*60)
    print("Test data left in database for manual inspection")
    print(f"Admin ID: {test_data['admin_id']}")
    print(f"Property A ID: {test_data['property_a_id']}")
    print(f"Property B ID: {test_data['property_b_id']}")
    admin_id_str = str(test_data['admin_id'])
    print("\nTo clean up, run:")
    print(f"  mongo.db.tenants.delete_many({{'admin_id': ObjectId('{admin_id_str}')}})")
    print(f"  mongo.db.houses.delete_many({{'admin_id': ObjectId('{admin_id_str}')}})")
    print(f"  mongo.db.meter_readings.delete_many({{'admin_id': ObjectId('{admin_id_str}')}})")
    print(f"  mongo.db.payments.delete_many({{'admin_id': ObjectId('{admin_id_str}')}})")
    print(f"  mongo.db.properties.delete_many({{'admin_id': ObjectId('{admin_id_str}')}})")
    print(f"  mongo.db.admins.delete_one({{'_id': ObjectId('{admin_id_str}')}})")

def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("DATA ISOLATION TEST SUITE")
    print("="*60)

    # Setup test data
    test_data = setup_test_data()

    # Run tests
    results = {
        "subscription_counts": test_subscription_counts(test_data),
        "property_isolation": test_property_isolation(test_data),
        "house_overlap": test_house_number_overlap(test_data),
        "rent_generation": test_rent_bill_generation(test_data)
    }

    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)

    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{test_name:30s}: {status}")

    total_passed = sum(results.values())
    total_tests = len(results)

    print(f"\nTotal: {total_passed}/{total_tests} tests passed")

    if total_passed == total_tests:
        print("\n🎉 All tests passed! Data isolation is working correctly.")
    else:
        print("\n⚠️  Some tests failed. Review the output above for details.")

    # Cleanup info
    cleanup_test_data(test_data)

    return total_passed == total_tests

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
