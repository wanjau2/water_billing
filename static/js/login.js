/* Login Page JavaScript - CSP Compliant */

document.addEventListener('DOMContentLoaded', function() {
    const togglePassword = document.getElementById('togglePassword');
    const password = document.getElementById('password');

    // Only initialize if elements exist (defensive programming)
    if (togglePassword && password) {
        togglePassword.addEventListener('click', function() {
            // Toggle password visibility
            const type = password.getAttribute('type') === 'password' ? 'text' : 'password';
            password.setAttribute('type', type);

            // Toggle eye icon
            const eyeIcon = this.querySelector('i');
            if (eyeIcon) {
                eyeIcon.classList.toggle('fa-eye');
                eyeIcon.classList.toggle('fa-eye-slash');
            }
        });
    }

    // Simple form validation
    const loginForm = document.getElementById('loginForm');
    if (loginForm) {
        loginForm.addEventListener('submit', function(event) {
            const usernameField = document.getElementById('username');
            const passwordField = document.getElementById('password');

            if (!usernameField || !passwordField) {
                return; // Exit if fields don't exist
            }

            const username = usernameField.value;
            const password = passwordField.value;

            if (!username || !password) {
                event.preventDefault();
                // Show validation error using browser's built-in validation
                if (!username) {
                    usernameField.focus();
                    usernameField.setCustomValidity('Please enter your username');
                    usernameField.reportValidity();
                } else if (!password) {
                    passwordField.focus();
                    passwordField.setCustomValidity('Please enter your password');
                    passwordField.reportValidity();
                }
                return false;
            } else {
                // Clear any custom validation messages
                usernameField.setCustomValidity('');
                passwordField.setCustomValidity('');
            }
        });
    }
});