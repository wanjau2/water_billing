/* CSP-compliant external JavaScript to replace inline event handlers */

// Get CSRF token from meta tag - utility function for all scripts
window.getCSRFToken = function() {
    const token = document.querySelector('meta[name="csrf-token"]');
    return token ? token.getAttribute('content') : '';
};

document.addEventListener('DOMContentLoaded', function() {
    // Initialize all event handlers
    initScrollToTop();
    initTenantPortalHandlers();
    initPaymentHandlers();
    initFilterHandlers();
    initDashboardHandlers();
    initChartExports();
    initSubscriptionProgressBars();
});

// Scroll to top functionality
function initScrollToTop() {
    const scrollToTopBtn = document.querySelector('[data-action="scroll-to-top"]');
    if (scrollToTopBtn) {
        scrollToTopBtn.addEventListener('click', function(e) {
            e.preventDefault();
            window.scrollTo(0, 0);
        });
    }
}

// Tenant portal handlers
function initTenantPortalHandlers() {
    // Refresh bills button
    const refreshBtn = document.querySelector('[data-action="refresh-bills"]');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', refreshBills);
    }

    // M-Pesa payment button
    const mpesaBtn = document.querySelector('[data-action="initiate-mpesa"]');
    if (mpesaBtn) {
        mpesaBtn.addEventListener('click', initiateM_PesaPayment);
    }

    // Navigation items
    const navItems = document.querySelectorAll('[data-nav-section]');
    navItems.forEach(item => {
        item.addEventListener('click', function(e) {
            e.preventDefault();
            const sectionId = this.getAttribute('data-nav-section');
            showSection(sectionId);
        });
    });
}

// Payment modal handlers
function initPaymentHandlers() {
    const paymentBtns = document.querySelectorAll('[data-action="open-payment-modal"]');
    paymentBtns.forEach(btn => {
        btn.addEventListener('click', function() {
            const billId = this.getAttribute('data-bill-id');
            const tenantName = this.getAttribute('data-tenant-name');
            const amount = this.getAttribute('data-amount');
            openPaymentModal(billId, tenantName, amount);
        });
    });

    const generateRentBtn = document.querySelector('[data-action="generate-rent-bills"]');
    if (generateRentBtn) {
        generateRentBtn.addEventListener('click', generateRentBills);
    }
}

// Filter handlers for garbage/bills
function initFilterHandlers() {
    const filterBtns = document.querySelectorAll('[data-filter]');
    filterBtns.forEach(btn => {
        btn.addEventListener('click', function() {
            const filter = this.getAttribute('data-filter');
            filterBills(filter);
        });
    });

    const viewDetailsBtns = document.querySelectorAll('[data-action="view-bill-details"]');
    viewDetailsBtns.forEach(btn => {
        btn.addEventListener('click', function() {
            const billId = this.getAttribute('data-bill-id');
            viewBillDetails(billId);
        });
    });
}

// Dashboard handlers
function initDashboardHandlers() {
    const exportBtns = document.querySelectorAll('[data-action="export-chart"]');
    exportBtns.forEach(btn => {
        btn.addEventListener('click', function() {
            const chartId = this.getAttribute('data-chart-id');
            const filename = this.getAttribute('data-filename');
            exportChart(chartId, filename);
        });
    });

    const generateReportBtn = document.querySelector('[data-action="generate-report"]');
    if (generateReportBtn) {
        generateReportBtn.addEventListener('click', generateReport);
    }
}

// Chart export functionality
function initChartExports() {
    // This function can be expanded for specific chart export needs
}

// Utility functions (to be implemented based on existing functionality)
function refreshBills() {
    // Implementation for refreshing bills
    console.log('Refreshing bills...');
    // Add your refresh logic here
}

function initiateM_PesaPayment() {
    // Implementation for M-Pesa payment
    console.log('Initiating M-Pesa payment...');
    // Add your M-Pesa logic here
}

function showSection(sectionId) {
    // Hide all sections
    document.querySelectorAll('.nav-section').forEach(section => {
        section.classList.add('d-none');
    });

    // Show target section
    const targetSection = document.getElementById(sectionId);
    if (targetSection) {
        targetSection.classList.remove('d-none');
    }

    // Update navigation active states
    document.querySelectorAll('[data-nav-section]').forEach(nav => {
        nav.classList.remove('active');
    });
    document.querySelector(`[data-nav-section="${sectionId}"]`)?.classList.add('active');
}

function openPaymentModal(billId, tenantName, amount) {
    // Implementation for opening payment modal
    console.log(`Opening payment modal for ${tenantName}, Bill: ${billId}, Amount: ${amount}`);
    // Add your modal logic here
}

function generateRentBills() {
    // Implementation for generating rent bills
    console.log('Generating rent bills...');
    // Add your generation logic here
}

function filterBills(filter) {
    // Implementation for filtering bills
    console.log(`Filtering bills by: ${filter}`);
    // Add your filter logic here
}

function viewBillDetails(billId) {
    // Implementation for viewing bill details
    console.log(`Viewing details for bill: ${billId}`);
    // Add your view logic here
}

function exportChart(chartId, filename) {
    // Implementation for chart export
    console.log(`Exporting chart ${chartId} as ${filename}`);
    // Add your export logic here
}

function generateReport() {
    // Implementation for report generation
    console.log('Generating report...');
    // Add your report logic here
}

// Password strength checker for signup forms
// Initialize subscription progress bars
function initSubscriptionProgressBars() {
    const progressBars = document.querySelectorAll('[data-width]');
    progressBars.forEach(bar => {
        const width = bar.getAttribute('data-width');
        if (width) {
            bar.style.width = width + '%';
        }
    });
}

function updatePasswordStrength(input, strengthBar, strengthText) {
    const value = input.value;
    let strength = 0;

    // Check length
    if (value.length >= 8) strength += 25;

    // Check for lowercase letters
    if (value.match(/[a-z]+/)) strength += 25;

    // Check for uppercase letters
    if (value.match(/[A-Z]+/)) strength += 25;

    // Check for numbers
    if (value.match(/[0-9]+/)) strength += 25;

    // Update strength indicator
    strengthBar.style.width = strength + '%';

    // Update color and text based on strength
    if (strength < 50) {
        strengthBar.className = 'progress-bar bg-danger';
        strengthText.textContent = 'Password strength: Weak';
        strengthText.className = 'text-danger small';
    } else if (strength < 75) {
        strengthBar.className = 'progress-bar bg-warning';
        strengthText.textContent = 'Password strength: Medium';
        strengthText.className = 'text-warning small';
    } else {
        strengthBar.className = 'progress-bar bg-success';
        strengthText.textContent = 'Password strength: Strong';
        strengthText.className = 'text-success small';
    }
}