# Paystack Integration - Quick Start Guide

## ✅ What's Been Integrated

Paystack is now fully integrated into your `/subscription` page! Here's what works:

### 1. **Subscription Page** (`/subscription`)
- Shows Paystack badge at top
- Each subscription tier now has:
  - **"Upgrade with Paystack"** button (primary - supports M-Pesa + Cards)
  - **"Pay with M-Pesa Direct"** button (optional secondary)
- Automatically shows based on `PAYMENT_PROVIDER` setting

### 2. **Payment Flow**
```
User clicks "Upgrade with Paystack"
    ↓
Modal opens → Select Monthly/Annual
    ↓
Click "Proceed to Secure Payment"
    ↓
Redirects to Paystack payment page
    ↓
User pays with M-Pesa, Card, or Bank
    ↓
Paystack processes payment
    ↓
Redirects back to /paystack/subscription-callback
    ↓
Subscription activated automatically!
```

### 3. **Routes Added**
- `POST /paystack/initiate-subscription-payment` - Start payment
- `GET /paystack/subscription-callback` - Handle return from Paystack
- `POST /paystack/webhook` - Receive real-time updates from Paystack

---

## 🚀 How to Enable Paystack

### Option 1: Paystack Only (Recommended)
```bash
# In .env file
PAYMENT_PROVIDER=paystack
PAYSTACK_SECRET_KEY=sk_test_your_key_here
PAYSTACK_PUBLIC_KEY=pk_test_your_key_here
PAYSTACK_ENV=test
```

**Result:** Users see only Paystack button (supports M-Pesa + Cards)

### Option 2: Both Paystack and M-Pesa
```bash
# In .env file
PAYMENT_PROVIDER=both
PAYSTACK_SECRET_KEY=sk_test_your_key_here
PAYSTACK_PUBLIC_KEY=pk_test_your_key_here
```

**Result:** Users see:
- Primary button: "Upgrade with Paystack" (M-Pesa + Cards)
- Secondary button: "Pay with M-Pesa Direct" (M-Pesa only)

### Option 3: M-Pesa Only (Current)
```bash
# In .env file
PAYMENT_PROVIDER=mpesa
```

**Result:** Nothing changes - only M-Pesa direct shows

---

## 📋 Setup Checklist

### Before Testing:

- [ ] **Get Paystack Account**
  - Go to https://paystack.com
  - Sign up
  - Verify email

- [ ] **Get Test API Keys**
  - Dashboard → Settings → API Keys
  - Copy Test Secret Key (`sk_test_...`)
  - Copy Test Public Key (`pk_test_...`)

- [ ] **Update .env**
  ```bash
  PAYMENT_PROVIDER=paystack
  PAYSTACK_SECRET_KEY=sk_test_your_actual_key
  PAYSTACK_PUBLIC_KEY=pk_test_your_actual_key
  PAYSTACK_ENV=test
  ```

- [ ] **Restart Flask App**
  ```bash
  # Stop current app (Ctrl+C)
  python home.py
  ```

- [ ] **Test Payment**
  - Go to `/subscription`
  - Click "Upgrade with Paystack"
  - Select Monthly/Annual
  - Click "Proceed to Secure Payment"
  - Use test card:
    - Card: 5531 8866 5214 2950
    - CVV: 564
    - Expiry: 09/32
    - PIN: 3310
    - OTP: 123456

---

## 🎯 What Each File Does

### `paystack_integration.py`
- **Purpose:** Paystack API wrapper
- **What it does:** Handles all communication with Paystack API
- **Key methods:**
  - `initialize_transaction()` - Start payment
  - `verify_transaction()` - Check if payment succeeded
  - `verify_webhook_signature()` - Validate webhook security

### `home.py` (Lines 1246-1467)
- **Purpose:** Payment routes
- **What it does:** Handles payment flow in your app
- **Routes:**
  - `/paystack/initiate-subscription-payment` - User clicks pay button
  - `/paystack/subscription-callback` - User returns after payment
  - `/paystack/webhook` - Paystack sends real-time updates

### `templates/subscription.html`
- **Purpose:** UI for payment selection
- **What it does:** Shows payment buttons and modals
- **Features:**
  - Payment provider badge
  - Paystack modal (M-Pesa + Cards)
  - M-Pesa direct modal (optional)

### `.env`
- **Purpose:** Configuration
- **Contains:** API keys, payment provider preference

---

## 🔄 Payment Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    /subscription Page                        │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ [Upgrade with Paystack] ← User clicks                 │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│              Paystack Modal Opens                            │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ ○ Monthly - KES 1,500                                 │  │
│  │ ○ Annual - KES 15,000 (Save 17%)                      │  │
│  │                                                        │  │
│  │ [Proceed to Secure Payment] ← User clicks             │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  POST /paystack/initiate-subscription-payment                │
│  • Validates tier                                            │
│  • Calculates amount                                         │
│  • Generates unique reference                                │
│  • Calls paystack_api.initialize_transaction()               │
│  • Saves payment record (status: pending)                    │
│  • Returns authorization_url                                 │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│            Redirects to Paystack Payment Page                │
│         https://checkout.paystack.com/xxx                    │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Pay KES 1,500                                          │  │
│  │                                                        │  │
│  │ [M-Pesa] [Visa/Mastercard] [Bank Transfer]            │  │
│  │                                                        │  │
│  │ User enters payment details ← User pays here          │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│              Paystack Processes Payment                      │
│  • Validates payment method                                  │
│  • Charges customer                                          │
│  • Sends webhook to your app (optional)                      │
│  • Redirects customer back to callback_url                   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  GET /paystack/subscription-callback?reference=SUB-xxx       │
│  • Calls paystack_api.verify_transaction(reference)          │
│  • Checks payment status (success/failed/abandoned)          │
│  • Updates payment record (status: completed)                │
│  • Calculates subscription dates                             │
│  • Updates admin.subscription_status = "active"              │
│  • Flashes success message                                   │
│  • Redirects to /subscription                                │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│              Back to /subscription Page                      │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ ✅ Payment successful! Your Starter subscription      │  │
│  │    is now active.                                      │  │
│  │                                                        │  │
│  │ Current Plan: Starter (Active)                         │  │
│  │ Expires: November 01, 2025 (30 days remaining)        │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 🧪 Testing Guide

### Test in Development (Local):

1. **Start ngrok** (for webhook testing):
```bash
ngrok http 5000
```

2. **Update .env** with ngrok URL:
```bash
PAYSTACK_CALLBACK_URL=https://your-ngrok-url.ngrok.io/paystack/subscription-callback
```

3. **Add webhook in Paystack dashboard:**
- Go to Settings → Webhooks
- Add: `https://your-ngrok-url.ngrok.io/paystack/webhook`

4. **Test payment:**
- Go to `/subscription`
- Click any "Upgrade with Paystack" button
- Select Monthly or Annual
- Use test card details
- Complete payment
- Verify subscription activated

### Test Cards (Sandbox):

**Successful Payment:**
```
Card: 5531 8866 5214 2950
CVV: 564
Expiry: 09/32
PIN: 3310
OTP: 123456
```

**Declined:**
```
Card: 5531 8866 5214 2950
CVV: 564
Expiry: 09/32
PIN: 0000  ← Use 0000 to simulate decline
```

**Insufficient Funds:**
```
Card: 5060 6666 6666 6666 6666
CVV: 123
Expiry: 09/32
PIN: 1234
OTP: 123456
```

---

## 🐛 Troubleshooting

### Problem: "paystack_api not defined"
**Solution:**
```bash
# Make sure PAYMENT_PROVIDER is set
echo $PAYMENT_PROVIDER  # Should show 'paystack' or 'both'

# Restart Flask app
python home.py
```

### Problem: "Payment initialization failed"
**Solution:**
- Check API keys in `.env`
- Verify keys start with `sk_test_` (not `sk_live_`)
- Check logs: `tail -f app.log | grep -i paystack`

### Problem: "Webhook not received"
**Solution:**
- Use ngrok for local testing
- Verify webhook URL in Paystack dashboard
- Check webhook logs in Paystack dashboard
- Ensure route has `@csrf.exempt`

### Problem: "Subscription not activated after payment"
**Solution:**
- Check callback route logs
- Manually verify payment in Paystack dashboard
- Check MongoDB: `db.subscription_payments.find({provider: 'paystack'})`

---

## 📊 Database Collections

### `subscription_payments`
Stores all payment attempts:

```javascript
{
  _id: ObjectId(...),
  admin_id: ObjectId(...),
  reference: "SUB-xxx-starter-20251001123045",
  tier: "starter",
  payment_type: "monthly",
  amount: 1500,
  status: "completed",  // pending, completed, failed
  provider: "paystack",
  created_at: ISODate(...),
  completed_at: ISODate(...),
  channel: "mobile_money",  // or "card"
  fees: 22.5,  // 1.5% of 1500
  authorization_url: "https://checkout.paystack.com/xxx",
  webhook_received: true
}
```

---

## 🎓 Key Concepts

### 1. **Reference**
- Unique ID for each payment attempt
- Format: `SUB-{admin_id}-{tier}-{timestamp}`
- Used to track payment through entire lifecycle

### 2. **Two-Step Verification**
- **Callback:** User returns to your site
- **Webhook:** Paystack notifies your site (more reliable)
- Always verify with `verify_transaction()` API call

### 3. **Amount in Kobo**
- Paystack uses smallest currency unit
- 1 KES = 100 kobo
- Always multiply by 100 when sending
- Always divide by 100 when receiving

### 4. **Channels**
- `['mobile_money', 'card']` - Allow both
- `['mobile_money']` - M-Pesa only
- `['card']` - Cards only

---

## 🚀 Going Live

When ready for production:

1. **Complete KYC** (Business verification)
2. **Get Live Keys** from Paystack dashboard
3. **Update .env:**
```bash
PAYSTACK_SECRET_KEY=sk_live_your_live_key
PAYSTACK_PUBLIC_KEY=pk_live_your_live_key
PAYSTACK_ENV=live
PAYSTACK_CALLBACK_URL=https://yourdomain.com/paystack/subscription-callback
```
4. **Update webhook URL** to production domain
5. **Test with small amount** (KES 10-50)
6. **Monitor first transactions** closely
7. **Go fully live!** 🎉

---

## 💰 Pricing

- **M-Pesa via Paystack:** 1.5% per transaction
- **Cards:** 2.9% per transaction (local cards)
- **International cards:** 3.9% + KES 100
- **Setup:** FREE
- **Monthly fees:** NONE

---

## 📞 Support

### Paystack Support
- Email: support@paystack.com
- Phone: +234 1 888 7278
- Docs: https://paystack.com/docs
- Dashboard: https://dashboard.paystack.com

### Your Integration
- Check logs: `tail -f app.log`
- Test routes: Use Postman/curl
- Database: Check MongoDB collections

---

**You're all set! 🎉**

Just add your Paystack API keys to `.env` and restart the app to start accepting payments!
