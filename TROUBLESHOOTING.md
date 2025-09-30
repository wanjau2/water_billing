# Water Billing System - Troubleshooting Guide

## 🚨 Common Issues and Solutions

### 1. Application Won't Start

#### Missing Environment Variables
**Error**: `ERROR: MONGO_URI not set in environment variables!`

**Solution**:
1. Copy the environment template: `cp .env.example .env`
2. Edit `.env` and set your configuration values
3. Ensure at least these variables are set:
   - `SECRET_KEY`
   - `MONGO_URI`
   - `DATABASE_NAME`

#### Database Connection Failed
**Error**: `MongoDB connection failed: [Errno 61] Connection refused`

**Solutions**:
- **MongoDB not running**: Start MongoDB service
  ```bash
  # On macOS with Homebrew
  brew services start mongodb-community

  # On Ubuntu/Debian
  sudo systemctl start mongod

  # On Windows
  net start MongoDB
  ```
- **Wrong connection string**: Check your `MONGO_URI` format:
  ```
  # Local MongoDB
  MONGO_URI=mongodb://localhost:27017

  # MongoDB Atlas
  MONGO_URI=mongodb+srv://username:password@cluster.mongodb.net/
  ```

### 2. Pages Not Rendering / Blank Pages

#### Missing Templates
**Error**: `TemplateNotFound: login.html`

**Solution**: Ensure all template files exist in `templates/` directory

#### CSS/JS Files Not Loading
**Error**: Console shows 404 errors for CSS/JS files

**Solutions**:
- Check that files exist in `static/css/` and `static/js/`
- Verify Flask's static file serving is working
- Check browser console for specific missing files

#### CSRF Token Errors
**Error**: `400 Bad Request: The CSRF token is missing`

**Solution**: Ensure CSRF tokens are included in forms:
```html
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}"/>
```

### 3. Import/Dependency Errors

#### Missing Python Packages
**Error**: `ModuleNotFoundError: No module named 'flask'`

**Solution**:
```bash
pip install -r requirements.txt
```

#### Conflicting Package Versions
**Solution**:
```bash
pip install --upgrade -r requirements.txt
```

### 4. M-Pesa Integration Issues

#### M-Pesa API Initialization Failed
**Error**: `M-Pesa API initialization failed`

**Solutions**:
- Check M-Pesa credentials in `.env` file
- Verify network connectivity
- Ensure sandbox/production environment is correctly set

### 5. CSP (Content Security Policy) Violations

#### Inline Script/Style Blocked
**Error**: Browser console shows CSP violations

**Solutions**:
- Use external CSS/JS files instead of inline styles/scripts
- Check that `login.css`, `signup.css`, `login.js`, `signup.js` files exist
- Verify CSP configuration in `home.py`

## 🔧 Quick Diagnostics

### 1. Run the Safe Startup Script
```bash
python run_app.py
```
This script validates your environment before starting the app.

### 2. Check Application Health
Visit `/health` endpoint to check application status:
```bash
curl http://localhost:5000/health
```

### 3. Check Logs
Application logs are written to `app.log`:
```bash
tail -f app.log
```

### 4. Test Database Connection
```python
python testmongo.py
```

## 🛠️ Environment Setup Checklist

- [ ] Python 3.8+ installed
- [ ] MongoDB installed and running
- [ ] Virtual environment activated (recommended)
- [ ] All packages installed: `pip install -r requirements.txt`
- [ ] `.env` file created with required variables
- [ ] MongoDB connection string correct
- [ ] Database name configured
- [ ] SECRET_KEY set (for production)

## 📞 Getting Help

1. **Check the logs**: Look at `app.log` for detailed error messages
2. **Use the health endpoint**: `/health` shows system status
3. **Run diagnostics**: Use `python run_app.py` for startup validation
4. **Check browser console**: For frontend issues, check browser developer tools

## 🔍 Debug Mode

For development, you can enable debug mode in `.env`:
```
FLASK_ENV=development
FLASK_DEBUG=1
```

This provides more detailed error messages but should **never** be used in production.