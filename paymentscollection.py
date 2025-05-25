from pymongo import MongoClient
from bson import ObjectId
import datetime

# Connect to MongoDB
client = MongoClient("mongodb+srv://wanjau2:Wanjau254@cluster0.bvu9fcw.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")  # Replace with your URI
db = client["Cluster0"]  # Replace with your DB name

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
