/**
 * CSRF Token Utility Functions
 * Provides comprehensive CSRF token management and validation
 */

class CSRFManager {
    constructor() {
        this.tokenMetaSelector = 'meta[name="csrf-token"]';
        this.tokenInputName = 'csrf_token';
        this.refreshEndpoint = '/api/csrf-token';
    }

    /**
     * Get CSRF token from meta tag
     * @returns {string|null} CSRF token or null if not found
     */
    getToken() {
        const metaTag = document.querySelector(this.tokenMetaSelector);
        if (!metaTag) {
            console.warn('CSRF meta tag not found');
            return null;
        }

        const token = metaTag.getAttribute('content');
        if (!token || token.trim() === '') {
            console.warn('CSRF token is empty');
            return null;
        }

        return token;
    }

    /**
     * Validate CSRF token format and presence
     * @param {string} token - Token to validate
     * @returns {boolean} True if token appears valid
     */
    validateToken(token) {
        if (!token || typeof token !== 'string') {
            return false;
        }

        // Basic validation - token should be a reasonable length
        if (token.length < 10) {
            console.warn('CSRF token appears too short');
            return false;
        }

        return true;
    }

    /**
     * Add CSRF token to form
     * @param {HTMLFormElement} form - Form element to add token to
     * @returns {boolean} True if token was added successfully
     */
    addTokenToForm(form) {
        if (!form || form.tagName !== 'FORM') {
            console.error('Invalid form element provided');
            return false;
        }

        // Remove existing CSRF token inputs
        const existingTokens = form.querySelectorAll(`input[name="${this.tokenInputName}"]`);
        existingTokens.forEach(input => input.remove());

        const token = this.getToken();
        if (!token || !this.validateToken(token)) {
            console.error('Cannot add invalid CSRF token to form');
            return false;
        }

        // Create and add CSRF token input
        const tokenInput = document.createElement('input');
        tokenInput.type = 'hidden';
        tokenInput.name = this.tokenInputName;
        tokenInput.value = token;

        form.appendChild(tokenInput);
        return true;
    }

    /**
     * Create a form with CSRF protection
     * @param {string} action - Form action URL
     * @param {string} method - HTTP method (default: POST)
     * @returns {HTMLFormElement|null} Form element with CSRF token or null if failed
     */
    createProtectedForm(action, method = 'POST') {
        const token = this.getToken();
        if (!token || !this.validateToken(token)) {
            console.error('Cannot create form - invalid CSRF token');
            return null;
        }

        const form = document.createElement('form');
        form.method = method;
        form.action = action;

        // Add CSRF token
        const tokenInput = document.createElement('input');
        tokenInput.type = 'hidden';
        tokenInput.name = this.tokenInputName;
        tokenInput.value = token;
        form.appendChild(tokenInput);

        return form;
    }

    /**
     * Validate all forms on the page have CSRF tokens
     * @returns {Array} Array of forms missing CSRF tokens
     */
    validatePageForms() {
        const forms = document.querySelectorAll('form[method="POST"], form[method="post"]');
        const formsWithoutTokens = [];

        forms.forEach(form => {
            const tokenInput = form.querySelector(`input[name="${this.tokenInputName}"]`);
            if (!tokenInput || !tokenInput.value || !this.validateToken(tokenInput.value)) {
                formsWithoutTokens.push(form);
            }
        });

        return formsWithoutTokens;
    }

    /**
     * Auto-fix forms missing CSRF tokens
     * @returns {number} Number of forms fixed
     */
    autoFixForms() {
        const formsWithoutTokens = this.validatePageForms();
        let fixedCount = 0;

        formsWithoutTokens.forEach(form => {
            if (this.addTokenToForm(form)) {
                fixedCount++;
                console.log('Added CSRF token to form:', form);
            }
        });

        return fixedCount;
    }

    /**
     * Add form submission validation
     * @param {HTMLFormElement} form - Form to add validation to
     */
    addSubmissionValidation(form) {
        if (!form || form.tagName !== 'FORM') {
            return;
        }

        form.addEventListener('submit', (event) => {
            const tokenInput = form.querySelector(`input[name="${this.tokenInputName}"]`);

            if (!tokenInput || !tokenInput.value) {
                event.preventDefault();
                alert('Security token missing. Please refresh the page and try again.');
                return false;
            }

            if (!this.validateToken(tokenInput.value)) {
                event.preventDefault();
                alert('Invalid security token. Please refresh the page and try again.');
                return false;
            }

            return true;
        });
    }

    /**
     * Initialize CSRF protection for the page
     */
    init() {
        // Check if CSRF token exists
        const token = this.getToken();
        if (!token) {
            console.error('CSRF token not found. Security features may not work properly.');
            return;
        }

        if (!this.validateToken(token)) {
            console.error('Invalid CSRF token format detected.');
            return;
        }

        // Auto-fix forms on page load
        const fixedCount = this.autoFixForms();
        if (fixedCount > 0) {
            console.log(`Fixed ${fixedCount} forms missing CSRF tokens`);
        }

        // Add validation to all POST forms
        const postForms = document.querySelectorAll('form[method="POST"], form[method="post"]');
        postForms.forEach(form => {
            this.addSubmissionValidation(form);
        });

        console.log('CSRF protection initialized successfully');
    }
}

// Create global instance
window.csrfManager = new CSRFManager();

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.csrfManager.init();
    });
} else {
    window.csrfManager.init();
}