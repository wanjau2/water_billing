#!/usr/bin/env python3
"""
Quick script to add email to existing admin account
"""

import os
from dotenv import load_dotenv
from pymongo import MongoClient
from bson.objectid import ObjectId

# Load environment variables
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME")

# Connect to MongoDB
client = MongoClient(MONGO_URI)
db = client[DATABASE_NAME]

# Your admin ID
admin_id = ObjectId("683f3dca76f29c7fc1633cd0")

# Get current admin
admin = db.admins.find_one({"_id": admin_id})

if admin:
    print(f"Current admin: {admin.get('name')}")
    print(f"Current email: {admin.get('email', 'NOT SET')}")
    print(f"Current phone: {admin.get('phone', 'NOT SET')}")

    # Prompt for email
    email = input("\nEnter email address for this admin: ").strip()

    if email and '@' in email:
        # Update admin with email
        db.admins.update_one(
            {"_id": admin_id},
            {"$set": {"email": email}}
        )
        print(f"\n✅ Email updated to: {email}")

        # Also fix subscription status for testing
        db.admins.update_one(
            {"_id": admin_id},
            {"$set": {
                "subscription_status": "active",
                "subscription_tier": "starter"
            }}
        )
        print("✅ Subscription status fixed")
    else:
        print("❌ Invalid email format")
else:
    print("❌ Admin not found")

client.close()
