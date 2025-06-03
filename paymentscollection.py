from pymongo import MongoClient
from bson import ObjectId
import datetime
import pymongo
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import os

# Connect to MongoDB
MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME")

try:
    # Create a new client and connect to the server
    client = MongoClient(MONGO_URI, server_api=ServerApi('1'))
    
    # Test the connection by accessing the database
    client.admin.command('ping')
    
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


# Schema validator
payments_schema = {
    "validator": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": [
                "admin_id", "tenant_id", "house_id", "bill_amount",
                "payment_status", "month_year", "created_at"
            ],
            "properties": {
                "admin_id": {"bsonType": "objectId"},
                "tenant_id": {"bsonType": "objectId"},
                "house_id": {"bsonType": "objectId"},
                "bill_amount": {"bsonType": "double"},
                "amount_paid": {"bsonType": ["double", "null"]},
                "payment_status": {
                    "bsonType": "string",
                    "enum": ["paid", "partial", "unpaid"]
                },
                "due_date": {"bsonType": ["date", "null"]},
                "month_year": {
                    "bsonType": "string",
                    "pattern": "^[0-9]{4}-(0[1-9]|1[0-2])$"
                },
                "reading_id": {"bsonType": ["objectId", "null"]},
                "payment_date": {"bsonType": ["date", "null"]},
                "payment_method": {"bsonType": ["string", "null"]},
                "notes": {"bsonType": ["string", "null"]},
                "created_at": {"bsonType": "date"},
                "updated_at": {"bsonType": ["date", "null"]}
            }
        }
    }
}

# Create or modify the collection with schema validation
if "payments" not in db.list_collection_names():
    db.create_collection("payments", **payments_schema)
    print("Created 'payments' collection with schema validation.")
else:
    db.command({
        "collMod": "payments",
        "validator": payments_schema["validator"],
        "validationLevel": "moderate"
    })
    print("'payments' collection already exists. Schema updated.")
