#!/usr/bin/env bash

# Update and install ODBC Driver for SQL Server
apt-get update && apt-get install -y \
  curl \
  apt-transport-https \
  gnupg

# Add Microsoft repo and install ODBC driver
curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
curl https://packages.microsoft.com/config/debian/11/prod.list > /etc/apt/sources.list.d/mssql-release.list
apt-get update && ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc-dev

# Ensure the virtual environment installs dependencies
pip install -r requirements.txt
