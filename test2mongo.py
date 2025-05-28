from pymongo import MongoClient
from pymongo.server_api import ServerApi
from dotenv import load_dotenv
import os

load_dotenv()
uri = os.getenv("MONGODB_URI")

try:
    client = MongoClient(uri, server_api=ServerApi('1'))
    client.admin.command('ping')
    print("MongoDB connection successful!")
    
    # Test a simple database operation
    db = client.test_database
    collection = db.test_collection
    
    # Insert a test document
    test_doc = {"test": "data", "timestamp": "2024"}
    result = collection.insert_one(test_doc)
    print(f"Test document inserted with ID: {result.inserted_id}")
    
    # Read it back
    found_doc = collection.find_one({"_id": result.inserted_id})
    print(f"Retrieved document: {found_doc}")
    
    # Clean up
    collection.delete_one({"_id": result.inserted_id})
    print("Test document cleaned up")
    
except Exception as e:
    print("MongoDB connection failed:", e)