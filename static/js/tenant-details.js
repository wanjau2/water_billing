document.addEventListener('DOMContentLoaded', function() {
    // Initialize chart data
    initializeBillingChart();

    // Initialize previous reading fetch
    initializePreviousReadingFetch();
});

function initializeBillingChart() {
    // Safely parse JSON data from Flask (will be injected by template)
    let labels = window.chartLabels || [];
    let usageData = window.chartUsageData || [];

    console.log('Labels:', labels);
    console.log('Usage Data:', usageData);

    // Ensure we have valid arrays
    if (!Array.isArray(labels)) labels = [];
    if (!Array.isArray(usageData)) usageData = [];

    // If no data is provided, use some dummy data for visualization
    if (labels.length === 0) {
        console.warn("No chart data provided. Using fallback dummy data.");
        labels = ["2025-01-01", "2025-02-01", "2025-03-01"];
        usageData = [50, 75, 60];
    }

    // Handle duplicate dates by adding time information
    const uniqueLabels = [];
    const uniqueUsageData = [];

    // Process data to handle duplicates
    for (let i = 0; i < labels.length; i++) {
        let label = labels[i];
        // If this date already exists, add a suffix
        let count = 0;
        for (let j = 0; j < i; j++) {
            if (labels[j] === label) count++;
        }
        if (count > 0) {
            label = `${label} (${count + 1})`;
        }
        uniqueLabels.push(label);
        uniqueUsageData.push(usageData[i]);
    }

    const ctx = document.getElementById('billingChart');
    if (!ctx) {
        console.error('Chart canvas not found');
        return;
    }

    const billingChart = new Chart(ctx.getContext('2d'), {
        type: 'line',
        data: {
            labels: uniqueLabels,
            datasets: [
                {
                    label: 'Water Usage (m³)',
                    data: uniqueUsageData,
                    borderColor: 'rgba(54, 162, 235, 1)',
                    backgroundColor: 'rgba(54, 162, 235, 0.2)',
                    borderWidth: 3,
                    tension: 0.4,
                    fill: true,
                    pointRadius: 5,
                    pointHoverRadius: 7
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                title: {
                    display: true,
                    text: 'Water Usage Trend',
                    font: {
                        size: 16
                    }
                },
                legend: {
                    position: 'top'
                },
                tooltip: {
                    mode: 'index',
                    intersect: false
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    title: {
                        display: true,
                        text: 'Water Usage (m³)',
                        font: {
                            weight: 'bold'
                        }
                    },
                    ticks: {
                        precision: 0
                    }
                },
                x: {
                    title: {
                        display: true,
                        text: 'Date',
                        font: {
                            weight: 'bold'
                        }
                    }
                }
            }
        }
    });
}

function initializePreviousReadingFetch() {
    const tenantId = window.tenantId;
    const previousReadingInput = document.getElementById('previous_reading');

    if (!tenantId || !previousReadingInput) {
        console.error('Tenant ID or previous reading input not found');
        return;
    }

    fetch(`/api/tenant/${tenantId}/readings`)
        .then(response => response.json())
        .then(data => {
            if (data && data.length > 0) {
                // Get the most recent reading (last item in the array)
                const lastReading = data[0];
                previousReadingInput.value = lastReading.current_reading;
            } else {
                // No previous readings, start from 0
                previousReadingInput.value = '0';
            }
        })
        .catch(error => {
            console.error('Error fetching readings:', error);
            previousReadingInput.value = '';
        });
}