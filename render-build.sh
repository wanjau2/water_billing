#!/usr/bin/env bash

# Update package lists and install required packages
sudo apt-get update
sudo apt-get install -y curl apt-transport-https gnupg

# Add Microsoft’s package signing key and repository
curl https://packages.microsoft.com/keys/microsoft.asc | sudo apt-key add -
curl https://packages.microsoft.com/config/ubuntu/22.04/prod.list | sudo tee /etc/apt/sources.list.d/mssql-release.list

# Update package lists again and install ODBC Driver 18 and unixODBC development headers
sudo apt-get update
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc-dev

# Finally, install Python dependencies
pip install -r requirements.txt
