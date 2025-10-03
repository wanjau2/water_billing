document.addEventListener('DOMContentLoaded', function() {
// Cache DOM elements
const elements = {
form: document.getElementById('signupForm'),
password: {
input: document.getElementById('password'),
toggle: document.getElementById('togglePassword'),
strength: {
bar: document.getElementById('password-strength'),
text: document.getElementById('password-strength-text')
}
},
confirmPassword: {
input: document.getElementById('confirm_password'),
match: document.getElementById('password-match')
},
payment: {
till: {
option: document.getElementById('till_option'),
input: document.getElementById('till_input'),
field: document.getElementById('till')
},
paybill: {
option: document.getElementById('paybill_option'),
input: document.getElementById('paybill_input'),
businessNumber: document.getElementById('business_number'),
accountName: document.getElementById('account_name')
}
},
name: document.getElementById('name'),
cost: document.getElementById('cost'),
terms: document.getElementById('terms')
};

// Initialize payment method toggle
initPaymentMethodToggle();

// Initialize password visibility toggle
initPasswordToggle();

// Initialize password strength checker
initPasswordStrengthChecker();

// Initialize password match checker
initPasswordMatchChecker();

// Initialize form validation
initFormValidation();

// Function to initialize payment method toggle
function initPaymentMethodToggle() {
// Set default state
if (elements.payment.till.option.checked) {
showTillInput();
} else if (elements.payment.paybill.option.checked) {
showPaybillInput();
} else {
// Default to till if nothing is selected
elements.payment.till.option.checked = true;
showTillInput();
}

// Add event listeners
elements.payment.till.option.addEventListener('change', function() {
if (this.checked) showTillInput();
});

elements.payment.paybill.option.addEventListener('change', function() {
if (this.checked) showPaybillInput();
});

function showTillInput() {
elements.payment.till.input.classList.remove('d-none');
elements.payment.paybill.input.classList.add('d-none');
elements.payment.till.field.setAttribute('required', '');
elements.payment.paybill.businessNumber.removeAttribute('required');
elements.payment.paybill.accountName.removeAttribute('required');
}

function showPaybillInput() {
elements.payment.paybill.input.classList.remove('d-none');
elements.payment.till.input.classList.add('d-none');
elements.payment.till.field.removeAttribute('required');
elements.payment.paybill.businessNumber.setAttribute('required', '');
elements.payment.paybill.accountName.setAttribute('required', '');
}
}

// Function to initialize password visibility toggle
function initPasswordToggle() {
elements.password.toggle.addEventListener('click', function() {
const type = elements.password.input.getAttribute('type') === 'password' ? 'text' : 'password';
elements.password.input.setAttribute('type', type);

// Toggle eye icon
const eyeIcon = this.querySelector('i');
eyeIcon.classList.toggle('fa-eye');
eyeIcon.classList.toggle('fa-eye-slash');
});
}

// Function to initialize password strength checker
function initPasswordStrengthChecker() {
elements.password.input.addEventListener('input', function() {
const value = this.value;
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
elements.password.strength.bar.style.width = strength + '%';

// Update color and text based on strength
if (strength < 50) {
elements.password.strength.bar.className = 'progress-bar bg-danger';
elements.password.strength.text.textContent = 'Password strength: Weak';
elements.password.strength.text.className = 'text-danger small';
} else if (strength < 75) {
elements.password.strength.bar.className = 'progress-bar bg-warning';
elements.password.strength.text.textContent = 'Password strength: Medium';
elements.password.strength.text.className = 'text-warning small';
} else {
elements.password.strength.bar.className = 'progress-bar bg-success';
elements.password.strength.text.textContent = 'Password strength: Strong';
elements.password.strength.text.className = 'text-success small';
}
});
}

// Function to initialize password match checker
function initPasswordMatchChecker() {
elements.confirmPassword.input.addEventListener('input', checkPasswordMatch);
elements.password.input.addEventListener('input', checkPasswordMatch);

function checkPasswordMatch() {
if (elements.password.input.value !== elements.confirmPassword.input.value) {
elements.confirmPassword.match.classList.remove('d-none');
} else {
elements.confirmPassword.match.classList.add('d-none');
}
}
}

// Function to initialize form validation
function initFormValidation() {
elements.form.addEventListener('submit', function(event) {
// Create an array to collect validation errors
const errors = [];

// Check if passwords match
if (elements.password.input.value !== elements.confirmPassword.input.value) {
errors.push('Passwords do not match');
elements.confirmPassword.match.classList.remove('d-none');
}

// Validate payment method fields
if (elements.payment.till.option.checked) {
const tillValue = elements.payment.till.field.value.trim();
if (!/^\d{6,10}$/.test(tillValue)) {
errors.push('Till number must be between 6-10 digits');
}
} else if (elements.payment.paybill.option.checked) {
const businessNumber = elements.payment.paybill.businessNumber.value.trim();
const accountName = elements.payment.paybill.accountName.value.trim();

if (!businessNumber) {
errors.push('Please enter a valid business number');
}

if (!accountName) {
errors.push('Please enter an account name');
}
} else {
errors.push('Please select a payment method');
}

// Check if all required fields are filled
if (!elements.name.value.trim()) {
errors.push('Please enter your full name');
}

if (!elements.password.input.value) {
errors.push('Please enter a password');
}

if (!elements.confirmPassword.input.value) {
errors.push('Please confirm your password');
}

if (!elements.cost.value) {
errors.push('Please enter cost per unit');
}

if (!elements.terms.checked) {
errors.push('Please accept the terms and conditions');
}

// If there are validation errors, prevent form submission and show the first error
if (errors.length > 0) {
event.preventDefault();
alert(errors[0]);
return false;
}

// Form is valid, allow submission
return true;
});
}
});
