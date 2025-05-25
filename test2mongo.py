from pymongo import MongoClient
import os

uri = os.getenv("MONGO_URI")

try:
    client = MongoClient(uri)
    client.admin.command('ping')
    print("Connected successfully to MongoDB.")
except Exception as e:
    print(f"An error occurred: {e}")
"""
from pymongo import MongoClient
import os
from dotenv import load_dotenv
import dns.resolver

# Load environment variables from .env file
load_dotenv()

# Get MongoDB URI from environment variables
uri = os.getenv("MONGO_URI")
print(f"Using MongoDB URI: {uri}")

# Test DNS resolution
try:
    print("Testing DNS resolution...")
    cluster_domain = uri.split('@')[1].split('/')[0]
    print(f"Cluster domain: {cluster_domain}")
    
    # Try to resolve the SRV record
    try:
        dns_result = dns.resolver.resolve(f"_mongodb._tcp.{cluster_domain}", 'SRV')
        print(f"DNS resolution successful: {dns_result}")
    except Exception as dns_err:
        print(f"DNS resolution failed: {dns_err}")
    
    # Try to connect to MongoDB
    print("Attempting MongoDB connection...")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    print("Connected successfully to MongoDB.")
    
    # List available databases
    print("Available databases:")
    for db_name in client.list_database_names():
        print(f"- {db_name}")
        
except Exception as e:
    print(f"An error occurred: {e}")
    print("Please check your MongoDB Atlas account to ensure the cluster is running")
    print("and that your network allows connections to MongoDB Atlas.")
"""