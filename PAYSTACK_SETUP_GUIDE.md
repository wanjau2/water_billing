# Paystack Integration Setup Guide

Complete guide to setting up Paystack payments for your Water Billing System

---

## Step 1: Create Paystack Account

### 1.1 Sign Up
1. Go to [https://paystack.com](https://paystack.com)
2. Click **"Get Started"** or **"Sign Up"**
3. Fill in:
   - Business Email
   - Business Name
   - Password
4. Verify your email

### 1.2 Complete Business Verification (KYC)
To accept live payments, you must complete verification:

1. **Login to Paystack Dashboard**
2. Go to **Settings → Business**
3. Upload required documents:
   - **Business Registration Certificate** (or Certificate of Incorporation)
   - **KRA PIN Certificate**
   - **Director's National ID/Passport**
   - **Business Bank Statement** (last 3-6 months)
   - **Proof of Business Address** (utility bill, lease agreement)

4. Fill in business details:
   - Business Name
   - Business Type (Sole Proprietor, Limited Company, etc.)
   - Business Address
   - Contact Person
   - Bank Account Details (for settlements)

5. **Wait for Approval** (Usually 1-3 business days)

---

## Step 2: Get API Keys

### 2.1 Test Keys (For Development)
1. Login to [Paystack Dashboard](https://dashboard.paystack.com)
2. Go to **Settings → API Keys & Webhooks**
3. Find **"Test Keys"** section
4. Copy:
   - **Test Secret Key** (starts with `sk_test_`)
   - **Test Public Key** (starts with `pk_test_`)

### 2.2 Live Keys (For Production - After Verification)
1. Once your account is verified
2. Go to **Settings → API Keys & Webhooks**
3. Find **"Live Keys"** section
4. Copy:
   - **Live Secret Key** (starts with `sk_live_`)
   - **Live Public Key** (starts with `pk_live_`)

⚠️ **IMPORTANT:** Never expose your secret keys in frontend code or public repositories!

---

## Step 3: Configure Your Application

### 3.1 Update .env File

Open `/home/biggie/wanjau/water_billing/.env` and add your keys:

```bash
# Paystack Configuration
PAYSTACK_SECRET_KEY=sk_test_xxxxxxxxxxxxxxxxxxxxx
PAYSTACK_PUBLIC_KEY=pk_test_xxxxxxxxxxxxxxxxxxxxx
PAYSTACK_ENV=test
PAYSTACK_CALLBACK_URL=https://yourdomain.com/paystack/subscription-callback
PAYSTACK_WEBHOOK_SECRET=  # Leave empty for now

# Payment Provider (paystack, mpesa, or both)
PAYMENT_PROVIDER=paystack
```

**For Production:**
```bash
PAYSTACK_SECRET_KEY=sk_live_xxxxxxxxxxxxxxxxxxxxx
PAYSTACK_PUBLIC_KEY=pk_live_xxxxxxxxxxxxxxxxxxxxx
PAYSTACK_ENV=live
PAYSTACK_CALLBACK_URL=https://yourdomain.com/paystack/subscription-callback
```

### 3.2 Update Your Domain
Replace `yourdomain.com` with your actual domain or:
- For local testing: `http://localhost:5000`
- For ngrok: `https://your-ngrok-url.ngrok.io`
- For production: `https://yourdomain.com`

---

## Step 4: Setup Webhooks

Webhooks notify your app when payments succeed/fail in real-time.

### 4.1 Get Webhook URL
Your webhook URL will be:
```
https://yourdomain.com/paystack/webhook
```

### 4.2 Configure in Paystack Dashboard
1. Go to **Settings → API Keys & Webhooks**
2. Scroll to **"Webhooks"** section
3. Click **"Add Webhook"**
4. Enter your webhook URL
5. Click **"Add"**

### 4.3 Get Webhook Secret (Optional but Recommended)
1. After adding webhook, Paystack will generate a **Webhook Secret**
2. Copy it and add to `.env`:
```bash
PAYSTACK_WEBHOOK_SECRET=sk_webhook_xxxxxxxxxxxxx
```

### 4.4 Test Webhook (Using ngrok for local development)

If testing locally:

1. **Install ngrok:**
```bash
# Download from https://ngrok.com/download
# Or install via snap
sudo snap install ngrok
```

2. **Start your Flask app:**
```bash
python home.py
```

3. **Start ngrok:**
```bash
ngrok http 5000
```

4. **Copy the ngrok URL** (e.g., `https://abc123.ngrok.io`)

5. **Update webhook URL in Paystack:**
```
https://abc123.ngrok.io/paystack/webhook
```

---

## Step 5: Integrate Paystack Routes into home.py

### 5.1 Add Import at Top of home.py

Find the import section (around line 7) and add:

```python
from paystack_integration import PaystackAPI
from paystack_routes import init_paystack_routes
```

### 5.2 Initialize Paystack Routes

Find where your app is initialized (after `app = Flask(__name__)`) and add:

```python
# After line where mongo is initialized
# Around line 150-200

# Initialize Paystack
if os.getenv('PAYMENT_PROVIDER') in ['paystack', 'both']:
    try:
        init_paystack_routes(app, mongo, csrf)
        app.logger.info("Paystack routes initialized")
    except Exception as e:
        app.logger.error(f"Failed to initialize Paystack: {e}")
```

---

## Step 6: Update Subscription Template

### 6.1 Add Paystack Payment Button

Open `/home/biggie/wanjau/water_billing/templates/subscription.html`

Find the subscription card payment buttons (around line 299) and update:

```html
<!-- BEFORE (M-Pesa only) -->
<button type="button" class="btn btn-subscription btn-upgrade w-100"
        data-bs-toggle="modal"
        data-bs-target="#paymentModal{{ tier_key }}">
    Upgrade
</button>

<!-- AFTER (Paystack - supports M-Pesa + Cards) -->
<form method="POST" action="{{ url_for('paystack_initiate_subscription_payment') }}">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <input type="hidden" name="tier" value="{{ tier_key }}">
    <input type="hidden" name="payment_type" value="monthly">
    <button type="submit" class="btn btn-subscription btn-upgrade w-100">
        💳 Pay with Paystack (M-Pesa/Card)
    </button>
</form>

<!-- Optional: Keep M-Pesa Direct as Alternative -->
<button type="button" class="btn btn-subscription btn-outline-primary w-100 mt-2"
        data-bs-toggle="modal"
        data-bs-target="#paymentModal{{ tier_key }}">
    📱 Pay with M-Pesa Direct
</button>
```

### 6.2 Add Payment Provider Indicator

Add this at the top of the subscription page to show which provider is active:

```html
{% if payment_provider == 'paystack' %}
<div class="alert alert-info">
    <i class="bi bi-credit-card"></i> Payments powered by
    <strong>Paystack</strong> - Accept M-Pesa, Cards, and Bank Transfers
</div>
{% endif %}
```

---

## Step 7: Testing

### 7.1 Test Cards (Paystack Sandbox)

Use these test card numbers in test mode:

**Successful Payment:**
```
Card Number: 5531 8866 5214 2950
CVV: 564
Expiry: 09/32
PIN: 3310
OTP: 123456
```

**Declined Card:**
```
Card Number: 5531 8866 5214 2950
CVV: 564
Expiry: 09/32
PIN: 0000
```

### 7.2 Test M-Pesa (Paystack Sandbox)

In test mode, use any Kenyan phone number format:
```
Phone: 254712345678
```

The payment will be simulated - no actual M-Pesa prompt.

### 7.3 Testing Checklist

- [ ] Can access subscription page
- [ ] Paystack button appears
- [ ] Clicking button redirects to Paystack
- [ ] Can enter card/phone details
- [ ] Payment processes successfully
- [ ] Redirected back to your site
- [ ] Subscription activated in database
- [ ] Payment record created
- [ ] Webhook received (check logs)

---

## Step 8: Go Live

### 8.1 Switch to Live Keys

1. Complete Paystack business verification (Step 1.2)
2. Get live API keys (Step 2.2)
3. Update `.env`:
```bash
PAYSTACK_SECRET_KEY=sk_live_xxxxxxxxxxxxx
PAYSTACK_PUBLIC_KEY=pk_live_xxxxxxxxxxxxx
PAYSTACK_ENV=live
```

4. Update webhook URL to production domain
5. Restart your application

### 8.2 Test with Small Amount

Before going fully live:
1. Make a small test payment (KES 10-50)
2. Verify it appears in Paystack dashboard
3. Check webhook logs
4. Verify database updates correctly
5. Check settlement to your bank account (T+2 days)

---

## Step 9: Monitor Payments

### 9.1 Paystack Dashboard
Monitor payments at: [https://dashboard.paystack.com/transactions](https://dashboard.paystack.com/transactions)

Features:
- View all transactions
- Export transaction reports
- Refund payments
- View settlement history
- Check webhook delivery status

### 9.2 Application Logs

Check your application logs:
```bash
tail -f /home/biggie/wanjau/water_billing/app.log | grep -i paystack
```

### 9.3 Database Queries

Check payment records:
```javascript
// In MongoDB
db.subscription_payments.find({provider: 'paystack'}).sort({created_at: -1}).limit(10)
```

---

## Step 10: Advanced Features (Optional)

### 10.1 Split Payments

If you want to auto-split payments (e.g., between property owner and manager):

```python
# Add to initialize_transaction call
subaccount = "ACCT_xxxxx"  # Create subaccount in Paystack dashboard

paystack_api.initialize_transaction(
    email=email,
    amount=amount,
    reference=reference,
    subaccount=subaccount,
    transaction_charge=500  # Keep KES 500 as platform fee
)
```

### 10.2 Recurring Payments

For automatic subscription renewals:

1. Enable authorization reuse in Paystack dashboard
2. Store `authorization_code` from first payment
3. Use Paystack Charge API for subsequent payments

### 10.3 Transfers API

To pay landlords/vendors:

```python
# Create recipient
result = paystack_api.create_transfer_recipient(
    account_name="John Doe",
    account_number="0123456789",
    bank_code="063"  # Diamond Trust Bank Kenya
)

# Initiate transfer
paystack_api.initiate_transfer(
    recipient_code=result['recipient_code'],
    amount=10000,  # KES 10,000
    reason="Property rental payment"
)
```

---

## Troubleshooting

### Issue: "Invalid API Key"
**Solution:**
- Verify you copied the correct key (secret key starts with `sk_`)
- Check for extra spaces in `.env` file
- Restart Flask application after updating `.env`

### Issue: "Transaction initialization failed"
**Solution:**
- Check email format is valid
- Ensure amount is positive
- Verify reference is unique
- Check Paystack API status: https://status.paystack.com

### Issue: "Webhook not received"
**Solution:**
- Verify webhook URL is publicly accessible (use ngrok for local testing)
- Check webhook logs in Paystack dashboard
- Ensure route has `@csrf.exempt` decorator
- Verify webhook secret is correct (if used)

### Issue: "Payment successful but subscription not activated"
**Solution:**
- Check application logs for errors
- Verify callback route is working
- Manually verify payment using Paystack dashboard
- Check MongoDB for payment record

### Issue: "Amount mismatch error"
**Solution:**
- Paystack amounts are in kobo (multiply by 100)
- Verify you're dividing by 100 when reading from Paystack
- Check for floating point precision issues

---

## Security Best Practices

### ✅ DO:
- Store API keys in environment variables (`.env`)
- Use HTTPS in production
- Verify webhook signatures
- Validate payment amounts server-side
- Log all transactions
- Implement rate limiting on payment routes
- Use test keys in development

### ❌ DON'T:
- Commit API keys to git
- Expose secret keys in frontend code
- Trust client-side payment amounts
- Skip signature verification on webhooks
- Use test keys in production
- Allow payments without authentication

---

## Support

### Paystack Support
- **Email:** support@paystack.com
- **Phone:** +234 1 888 7278
- **Docs:** https://paystack.com/docs
- **Status:** https://status.paystack.com

### Additional Resources
- **API Reference:** https://paystack.com/docs/api/
- **Mobile Money:** https://paystack.com/docs/payments/mobile-money
- **Webhooks:** https://paystack.com/docs/payments/webhooks
- **Testing:** https://paystack.com/docs/payments/test-payments

---

## Comparison: Paystack vs M-Pesa Direct

| Feature | Paystack | M-Pesa Direct |
|---------|----------|---------------|
| **Transaction Fee** | 1.5% | Variable (up to 10%) |
| **Payment Methods** | M-Pesa + Cards + Bank | M-Pesa only |
| **Setup Complexity** | Easy (API keys only) | Medium (OAuth, certs) |
| **Settlement** | Bank/M-Pesa (T+2) | M-Pesa Paybill only |
| **Multi-currency** | Yes (KES, USD, etc.) | KES only |
| **Recurring Billing** | Built-in | Manual implementation |
| **Refunds** | Dashboard + API | Manual via M-Pesa |
| **Reports** | Comprehensive | Basic |
| **International** | Yes | Kenya only |

---

## Next Steps

After setup:

1. ✅ Test with test keys thoroughly
2. ✅ Complete Paystack verification
3. ✅ Switch to live keys
4. ✅ Monitor first few transactions closely
5. ✅ Add Paystack logo to your site
6. ✅ Consider keeping M-Pesa direct as backup
7. ✅ Explore split payments and transfers
8. ✅ Set up automated reports

---

**Setup Complete! 🎉**

You can now accept payments via M-Pesa, Visa, Mastercard, and bank transfers through Paystack.

For questions or issues, refer to the troubleshooting section or contact Paystack support.
