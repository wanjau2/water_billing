/* Subscription page JavaScript - CSP compliant */

// Get CSRF token from meta tag
function getCSRFToken() {
    return document.querySelector('meta[name="csrf-token"]').getAttribute('content');
}

document.addEventListener('DOMContentLoaded', function() {
    initAutoRenewToggle();
    initPaymentButtons();
});

// Handle auto-renew toggle
function initAutoRenewToggle() {
    const autoRenewSwitch = document.getElementById('autoRenewSwitch');
    if (autoRenewSwitch) {
        autoRenewSwitch.addEventListener('change', function() {
            fetch('/toggle_auto_renew', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                }
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    alert(data.message);
                } else {
                    alert('Error: ' + data.error);
                    this.checked = !this.checked;
                }
            })
            .catch(error => {
                console.error('Error:', error);
                alert('An error occurred. Please try again.');
                this.checked = !this.checked;
            });
        });
    }
}

// Handle payment buttons
function initPaymentButtons() {
    // Handle proceed payment buttons from modals
    document.querySelectorAll('.proceed-payment').forEach(button => {
        button.addEventListener('click', function() {
            const tier = this.dataset.tier;
            const modal = document.getElementById(`paymentModal${tier}`);
            const form = modal.querySelector('.subscription-form');
            const paymentType = modal.querySelector(`input[name="payment_type_${tier}"]:checked`).value;
            const phoneNumber = modal.querySelector(`#phone_${tier}`).value;

            // Close current modal
            const bsModal = bootstrap.Modal.getInstance(modal);
            bsModal.hide();

            // Show progress modal
            const progressModal = new bootstrap.Modal(document.getElementById('paymentProgressModal'));
            progressModal.show();

            // Submit payment
            initiatePayment(tier, paymentType, phoneNumber, progressModal);
        });
    });
}

// Initiate payment
function initiatePayment(tier, paymentType, phoneNumber, progressModal) {

    fetch('/initiate_subscription_payment', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-CSRFToken': getCSRFToken()
        },
        body: `tier=${tier}&payment_type=${paymentType}&phone_number=${phoneNumber}`
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Start polling for payment status
            if (data.checkout_request_id) {
                pollPaymentStatus(data.checkout_request_id, progressModal);
            } else {
                // Free tier activated immediately
                progressModal.hide();
                alert(data.message);
                window.location.reload();
            }
        } else {
            progressModal.hide();
            alert('Error: ' + data.error);
        }
    })
    .catch(error => {
        progressModal.hide();
        console.error('Error:', error);
        alert('An error occurred. Please try again.');
    });
}

// Poll payment status
function pollPaymentStatus(checkoutRequestId, progressModal) {
    let attempts = 0;
    const maxAttempts = 60; // 5 minutes max

    const interval = setInterval(() => {
        attempts++;

        fetch(`/check_payment_status/${checkoutRequestId}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'completed') {
                clearInterval(interval);
                progressModal.hide();
                alert('Payment successful! Your subscription has been activated.');
                window.location.reload();
            } else if (data.status === 'failed') {
                clearInterval(interval);
                progressModal.hide();
                alert('Payment failed. Please try again.');
            } else if (attempts >= maxAttempts) {
                clearInterval(interval);
                progressModal.hide();
                alert('Payment timeout. Please check your payment history.');
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Status check error:', error);
        });
    }, 5000); // Check every 5 seconds
}