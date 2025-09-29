/**
 * Common JavaScript utilities for Water Billing System
 * CSP-compliant event handling and utility functions
 */

// Utility function to show/hide sections in tenant portal
function showSection(sectionId) {
    // Hide all sections
    const sections = document.querySelectorAll('.content-section');
    sections.forEach(section => {
        section.style.display = 'none';
    });

    // Show target section
    const targetSection = document.getElementById(sectionId);
    if (targetSection) {
        targetSection.style.display = 'block';
    }

    // Update navigation
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(item => {
        item.classList.remove('active');
    });

    const activeNavItem = document.querySelector(`[data-section="${sectionId}"]`);
    if (activeNavItem) {
        activeNavItem.classList.add('active');
    }
}

// Payment modal functions
function openPaymentModal(billId, tenantName, amount) {
    const modal = document.getElementById('paymentModal');
    const form = document.getElementById('paymentForm');
    const tenantNameEl = document.getElementById('tenantName');
    const amountEl = document.getElementById('amount');
    const billIdEl = document.getElementById('billId');

    if (tenantNameEl) tenantNameEl.textContent = tenantName;
    if (amountEl) amountEl.value = amount;
    if (billIdEl) billIdEl.value = billId;
    if (form) form.action = `/record_payment/${billId}`;
}

// Rent bill generation
function generateRentBills() {
    if (confirm('Generate rent bills for all tenants?')) {
        window.location.href = '/generate_rent_bills';
    }
}

// Garbage bill generation
function generateGarbageBills() {
    const modal = new bootstrap.Modal(document.getElementById('generateBillsModal'));
    modal.show();
}

// Chart export functions
function exportChart(chartId, filename) {
    const chart = Chart.getChart(chartId);
    if (chart) {
        const url = chart.toBase64Image();
        const link = document.createElement('a');
        link.download = `${filename}.png`;
        link.href = url;
        link.click();
    }
}

function generateReport() {
    if (confirm('Generate comprehensive analytics report?')) {
        window.location.href = '/generate_report';
    }
}

// M-Pesa payment initiation
function initiateMPesaPayment() {
    const phone = document.getElementById('mpesa-phone');
    const amount = document.getElementById('mpesa-amount');

    if (!phone || !amount || !phone.value || !amount.value) {
        showAlert('Please fill in all required fields', 'error');
        return;
    }

    // Here you would normally make an AJAX call to initiate M-Pesa payment
    showAlert('M-Pesa payment initiated. Please check your phone for the payment prompt.', 'success');
}

// Refresh functions
function refreshBills() {
    window.location.reload();
}

// Form validation
function validateForm(formId) {
    const form = document.getElementById(formId);
    if (!form) return false;

    const requiredFields = form.querySelectorAll('[required]');
    let isValid = true;

    requiredFields.forEach(field => {
        if (!field.value.trim()) {
            field.classList.add('is-invalid');
            isValid = false;
        } else {
            field.classList.remove('is-invalid');
        }
    });

    return isValid;
}

// Alert function to replace alert()
function showAlert(message, type = 'info') {
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} alert-dismissible fade show`;
    alertDiv.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;

    const container = document.querySelector('.container') || document.body;
    container.insertBefore(alertDiv, container.firstChild);

    // Auto-dismiss after 5 seconds
    setTimeout(() => {
        if (alertDiv.parentNode) {
            alertDiv.remove();
        }
    }, 5000);
}

// Password validation
function validatePasswords(newPasswordId, confirmPasswordId) {
    const newPassword = document.getElementById(newPasswordId);
    const confirmPassword = document.getElementById(confirmPasswordId);

    if (newPassword && confirmPassword) {
        if (newPassword.value !== confirmPassword.value) {
            showAlert('Passwords do not match!', 'error');
            return false;
        }
    }
    return true;
}

// DOM Ready initialization
document.addEventListener('DOMContentLoaded', function() {
    // Initialize confirm dialogs
    const confirmLinks = document.querySelectorAll('[data-action="confirm-link"]');
    confirmLinks.forEach(link => {
        link.addEventListener('click', function(e) {
            const message = this.dataset.confirm;
            if (message && !confirm(message)) {
                e.preventDefault();
                return false;
            }
        });
    });

    // Initialize section navigation for tenant portal
    const sectionButtons = document.querySelectorAll('[data-section]');
    sectionButtons.forEach(button => {
        button.addEventListener('click', function(e) {
            e.preventDefault();
            const sectionId = this.dataset.section;
            if (sectionId) {
                showSection(sectionId);
            }
        });
    });

    // Initialize payment modal buttons
    const paymentBtns = document.querySelectorAll('.payment-btn');
    paymentBtns.forEach(btn => {
        btn.addEventListener('click', function() {
            const billId = this.dataset.billId;
            const tenantName = this.dataset.tenantName;
            const amount = this.dataset.amount;

            if (billId && tenantName && amount) {
                openPaymentModal(billId, tenantName, amount);
            }
        });
    });

    // Initialize form validation
    const forms = document.querySelectorAll('form[data-validate]');
    forms.forEach(form => {
        form.addEventListener('submit', function(e) {
            const formId = this.id;
            if (formId && !validateForm(formId)) {
                e.preventDefault();
                showAlert('Please fill in all required fields', 'error');
            }
        });
    });

    // Initialize password confirmation
    const passwordForms = document.querySelectorAll('form[data-password-confirm]');
    passwordForms.forEach(form => {
        form.addEventListener('submit', function(e) {
            if (!validatePasswords('new_password', 'confirm_password')) {
                e.preventDefault();
            }
        });
    });

    // Initialize file upload spinners
    const importForms = document.querySelectorAll('form[data-spinner]');
    importForms.forEach(form => {
        form.addEventListener('submit', function() {
            const spinnerId = this.dataset.spinner;
            const spinner = document.getElementById(spinnerId);
            if (spinner) {
                spinner.style.display = 'block';
            }
        });
    });
});