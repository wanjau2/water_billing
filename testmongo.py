# test_mongo.py
from pymongo import MongoClient
from pymongo.server_api import ServerApi
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME")

try:
    client = MongoClient(MONGO_URI, server_api=ServerApi('1'))
    client.admin.command('ping')
    print("Successfully connected to MongoDB!")
    
    # List databases
    print("Available databases:", client.list_database_names())
    
except Exception as e:
    print(f"Connection failed: {type(e)._name_}: {e}")