document.addEventListener('DOMContentLoaded', function() {
    // Initialize all functionality
    initializeFAQToggles();
    initializeMobileMenu();
    initializePricingToggle();
});

function initializeFAQToggles() {
    const faqButtons = document.querySelectorAll('.bg-white button');

    faqButtons.forEach(button => {
        button.addEventListener('click', function() {
            const content = this.nextElementSibling;
            const icon = this.querySelector('i');

            if (!content || !icon) return;

            if (content.style.display === 'none' || content.style.display === '') {
                content.style.display = 'block';
                icon.classList.remove('fa-chevron-down');
                icon.classList.add('fa-chevron-up');
            } else {
                content.style.display = 'none';
                icon.classList.remove('fa-chevron-up');
                icon.classList.add('fa-chevron-down');
            }
        });
    });
}

function initializeMobileMenu() {
    const mobileMenuButton = document.querySelector('button.md\\:hidden');
    const mobileMenu = document.querySelector('nav.hidden.md\\:flex');

    if (mobileMenuButton && mobileMenu) {
        mobileMenuButton.addEventListener('click', function() {
            // Check if the menu is currently hidden
            const isHidden = mobileMenu.classList.contains('hidden');

            // Toggle the hidden class
            if (isHidden) {
                mobileMenu.classList.remove('hidden');
                mobileMenu.classList.add('flex', 'flex-col', 'absolute', 'top-16', 'left-0', 'right-0', 'bg-white', 'shadow-md', 'p-4', 'z-50');
            } else {
                mobileMenu.classList.add('hidden');
                mobileMenu.classList.remove('flex', 'flex-col', 'absolute', 'top-16', 'left-0', 'right-0', 'bg-white', 'shadow-md', 'p-4', 'z-50');
            }
        });
    }
}

function initializePricingToggle() {
    const monthlyBtn = document.getElementById('monthlyBtn');
    const annualBtn = document.getElementById('annualBtn');
    const monthlyPrices = document.querySelectorAll('.monthly-price');
    const annualPrices = document.querySelectorAll('.annual-price');

    // Check if elements exist
    if (!monthlyBtn || !annualBtn) {
        console.warn('Pricing toggle buttons not found');
        return;
    }

    if (monthlyPrices.length === 0 || annualPrices.length === 0) {
        console.warn('Pricing elements not found');
        return;
    }

    // Monthly button click handler
    monthlyBtn.addEventListener('click', function() {
        // Update button states
        updateButtonState(monthlyBtn, annualBtn);

        // Show monthly prices, hide annual prices
        showPrices(monthlyPrices, annualPrices);
    });

    // Annual button click handler
    annualBtn.addEventListener('click', function() {
        // Update button states
        updateButtonState(annualBtn, monthlyBtn);

        // Show annual prices, hide monthly prices
        showPrices(annualPrices, monthlyPrices);
    });

    // Helper function to update button states
    function updateButtonState(activeBtn, inactiveBtn) {
        // Active button styling
        activeBtn.classList.add('bg-blue-600', 'text-white');
        activeBtn.classList.remove('text-gray-700', 'hover:text-gray-900');

        // Inactive button styling
        inactiveBtn.classList.remove('bg-blue-600', 'text-white');
        inactiveBtn.classList.add('text-gray-700', 'hover:text-gray-900');
    }

    // Helper function to show/hide prices
    function showPrices(showElements, hideElements) {
        // Show target elements
        showElements.forEach(el => {
            el.classList.remove('hidden');
            el.style.display = 'block';
        });

        // Hide other elements
        hideElements.forEach(el => {
            el.classList.add('hidden');
            el.style.display = 'none';
        });
    }

    // Initialize with monthly view by default
    updateButtonState(monthlyBtn, annualBtn);
    showPrices(monthlyPrices, annualPrices);
}