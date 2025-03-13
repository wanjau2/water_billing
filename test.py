import os
from dotenv import load_dotenv
import pyodbc

# Load environment variables from .env file
load_dotenv()

# Print available ODBC drivers for verification
print("Available ODBC drivers:", pyodbc.drivers())

# Get connection parameters from environment variables
server = os.environ.get("AZURE_SERVER")
database = os.environ.get("AZURE_DATABASE")
username = os.environ.get("AZURE_USERNAME")
password = os.environ.get("AZURE_PASSWORD")
driver = "{ODBC Driver 18 for SQL Server}"

# Check if all required variables are set
if not all([server, database, username, password]):
    print("Missing one or more required environment variables.")
else:
    # Build the connection string
    conn_str = (
        f"DRIVER={driver};"
        f"SERVER={server};"
        f"PORT=1433;"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password}"
    )
    
    print("Using connection string:", conn_str)
    
    try:
        # Attempt to establish a connection
        conn = pyodbc.connect(conn_str)
        print("✅ Connection successful!")
        cursor = conn.cursor()
        cursor.execute("SELECT @@VERSION")
        version = cursor.fetchone()
        print("SQL Server Version:", version)
        conn.close()
    except Exception as e:
        print(f"❌ Connection failed: {e}")
