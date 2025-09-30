#!/usr/bin/env python3
"""
Safe startup script for Water Billing System
Validates environment and dependencies before starting the application
"""

import os
import sys
from pathlib import Path

def check_environment():
    """Check if all required environment variables are set"""
    print("🔍 Checking environment configuration...")

    # Check if .env file exists
    if not Path('.env').exists():
        print("⚠️  .env file not found!")
        print("📋 Creating .env from template...")

        if Path('.env.example').exists():
            print("Please copy .env.example to .env and configure your settings:")
            print("cp .env.example .env")
            return False
        else:
            print("❌ .env.example not found. Please create .env file manually.")
            return False

    # Load environment variables
    from dotenv import load_dotenv
    load_dotenv()

    # Check critical environment variables
    required_vars = [
        'SECRET_KEY',
        'MONGO_URI',
        'DATABASE_NAME'
    ]

    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)

    if missing_vars:
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        print("Please set these variables in your .env file")
        return False

    print("✅ Environment configuration looks good")
    return True

def check_dependencies():
    """Check if all required Python packages are installed"""
    print("📦 Checking Python dependencies...")

    required_packages = [
        'flask',
        'pymongo',
        'flask_talisman',
        'flask_limiter',
        'python-dotenv',
        'werkzeug',
        'bson'
    ]

    missing_packages = []
    for package in required_packages:
        try:
            __import__(package.replace('-', '_'))
        except ImportError:
            missing_packages.append(package)

    if missing_packages:
        print(f"❌ Missing required packages: {', '.join(missing_packages)}")
        print("Please install them with: pip install -r requirements.txt")
        return False

    print("✅ All required packages are installed")
    return True

def test_database_connection():
    """Test MongoDB connection"""
    print("🗄️  Testing database connection...")

    from dotenv import load_dotenv
    load_dotenv()

    try:
        from pymongo import MongoClient
        from pymongo.server_api import ServerApi

        mongo_uri = os.getenv('MONGO_URI')
        database_name = os.getenv('DATABASE_NAME')

        if not mongo_uri or not database_name:
            print("❌ MongoDB configuration not found")
            return False

        client = MongoClient(mongo_uri, server_api=ServerApi('1'))
        client.admin.command('ping')

        print("✅ Database connection successful")
        return True

    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        print("Please check your MongoDB configuration and ensure MongoDB is running")
        return False

def main():
    """Main startup validation"""
    print("🚀 Water Billing System - Startup Validation")
    print("=" * 50)

    # Run all checks
    checks = [
        check_environment,
        check_dependencies,
        test_database_connection
    ]

    for check in checks:
        if not check():
            print("\n❌ Startup validation failed!")
            print("Please fix the issues above before starting the application")
            sys.exit(1)

    print("\n" + "=" * 50)
    print("✅ All checks passed! Starting application...")
    print("=" * 50)

    # Import and run the application
    try:
        from home import app
        print("🌐 Starting Flask development server...")
        print("💡 Access the application at: http://localhost:5000")
        print("🛑 Press Ctrl+C to stop the server")
        app.run(debug=True, host='0.0.0.0', port=5000)

    except KeyboardInterrupt:
        print("\n👋 Application stopped by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Failed to start application: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()