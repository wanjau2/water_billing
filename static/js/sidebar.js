document.addEventListener('DOMContentLoaded', function() {
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebar = document.getElementById('default-sidebar');
    const mainContent = document.getElementById('main-content');
    const sidebarTexts = document.querySelectorAll('.sidebar-text');
    const sidebarBrand = document.querySelector('.sidebar-brand');
    const utilitiesDropdown = document.querySelector('.utilities-dropdown');
    const waterDirect = document.querySelector('.water-direct');
    const garbageDirect = document.querySelector('.garbage-direct');

    // Check if required elements exist
    if (!sidebarToggle || !sidebar || !mainContent) {
        console.warn('Sidebar elements not found. Sidebar functionality disabled.');
        return;
    }

    let isCollapsed = false;

    // Handle mobile responsiveness
    function handleMobileView() {
        if (window.innerWidth < 768) {
            sidebar.classList.add('-translate-x-full', 'md:translate-x-0');
        } else {
            sidebar.classList.remove('-translate-x-full');
        }
    }

    // Initial mobile check
    handleMobileView();
    window.addEventListener('resize', handleMobileView);

    sidebarToggle.addEventListener('click', function() {
        // Handle mobile and desktop differently
        if (window.innerWidth < 768) {
            // Mobile: toggle sidebar visibility
            sidebar.classList.toggle('-translate-x-full');
        } else {
            // Desktop: toggle sidebar width
            isCollapsed = !isCollapsed;
            localStorage.setItem('sidebarCollapsed', isCollapsed);

            if (isCollapsed) {
                // Collapse sidebar
                sidebar.classList.remove('w-64');
                sidebar.classList.add('w-16');
                mainContent.classList.remove('md:ml-64');
                mainContent.classList.add('md:ml-16');

                // Hide text elements
                sidebarTexts.forEach(text => {
                    text.classList.add('hidden');
                });
                if (sidebarBrand) sidebarBrand.classList.add('hidden');

                // Hide utilities dropdown, show direct links
                if (utilitiesDropdown) utilitiesDropdown.classList.add('hidden');
                if (waterDirect) waterDirect.classList.remove('hidden');
                if (garbageDirect) garbageDirect.classList.remove('hidden');

            } else {
                // Expand sidebar
                sidebar.classList.remove('w-16');
                sidebar.classList.add('w-64');
                mainContent.classList.remove('md:ml-16');
                mainContent.classList.add('md:ml-64');

                // Show text elements
                sidebarTexts.forEach(text => {
                    text.classList.remove('hidden');
                });
                if (sidebarBrand) sidebarBrand.classList.remove('hidden');

                // Show utilities dropdown, hide direct links
                if (utilitiesDropdown) utilitiesDropdown.classList.remove('hidden');
                if (waterDirect) waterDirect.classList.add('hidden');
                if (garbageDirect) garbageDirect.classList.add('hidden');
            }
        }
    });

    // Restore saved state
    const savedState = localStorage.getItem('sidebarCollapsed');
    if (savedState === 'true') {
        sidebarToggle.click();
    }

    // Property dropdown functionality
    const propertyDropdownToggle = document.getElementById('property-dropdown-toggle');
    const propertyDropdownMenu = document.getElementById('property-dropdown-menu');
    const currentPropertyName = document.getElementById('current-property-name');
    const propertiesList = document.getElementById('properties-list');

    if (propertyDropdownToggle) {
        // Load properties on page load
        loadProperties();

        // Toggle dropdown
        propertyDropdownToggle.addEventListener('click', function(e) {
            e.stopPropagation();
            propertyDropdownMenu.classList.toggle('hidden');
        });

        // Close dropdown when clicking outside
        document.addEventListener('click', function(e) {
            if (!propertyDropdownToggle.contains(e.target) && !propertyDropdownMenu.contains(e.target)) {
                propertyDropdownMenu.classList.add('hidden');
            }
        });
    }

    function loadProperties() {
        fetch('/api/properties')
            .then(response => response.json())
            .then(data => {
                if (data.properties) {
                    // Update current property name
                    const currentProperty = data.properties.find(p => p.is_current);
                    if (currentProperty) {
                        currentPropertyName.textContent = currentProperty.name;
                    }

                    // Populate dropdown
                    propertiesList.innerHTML = '';
                    data.properties.forEach(property => {
                        const item = document.createElement('div');
                        if (property.is_current) {
                            item.innerHTML = `
                                <div class="px-4 py-2 text-sm text-gray-900 bg-blue-50">
                                    <div class="flex items-center justify-between">
                                        <span>${property.name}</span>
                                        <span class="text-blue-600 text-xs">Current</span>
                                    </div>
                                </div>
                            `;
                        } else {
                            // Enhanced CSRF token handling with validation
                            const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

                            // Validate CSRF token exists
                            if (!csrfToken) {
                                console.warn('CSRF token not found. Property switching may fail.');
                                item.innerHTML = `
                                    <div class="px-4 py-2 text-sm text-red-600">
                                        <i class="fas fa-exclamation-triangle mr-2"></i>
                                        Security token missing - refresh page
                                    </div>
                                `;
                                return;
                            }

                            // Create form with enhanced CSRF protection
                            const form = document.createElement('form');
                            form.method = 'POST';
                            form.action = `/switch_property/${property.id}`;
                            form.className = 'm-0';

                            // Add CSRF token with validation
                            const csrfInput = document.createElement('input');
                            csrfInput.type = 'hidden';
                            csrfInput.name = 'csrf_token';
                            csrfInput.value = csrfToken;
                            form.appendChild(csrfInput);

                            // Create submit button
                            const button = document.createElement('button');
                            button.type = 'submit';
                            button.className = 'w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-100';
                            button.textContent = property.name;

                            // Add form submission handler for error handling
                            form.addEventListener('submit', function(e) {
                                // Double-check CSRF token before submission
                                if (!csrfInput.value) {
                                    e.preventDefault();
                                    alert('Security token missing. Please refresh the page and try again.');
                                    return false;
                                }

                                // Show loading state
                                button.disabled = true;
                                button.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Switching...';
                            });

                            form.appendChild(button);
                            item.appendChild(form);
                        }
                        propertiesList.appendChild(item);
                    });
                }
            })
            .catch(error => {
                console.error('Error loading properties:', error);
            });
    }
});