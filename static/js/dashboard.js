let charts = {};

document.addEventListener('DOMContentLoaded', function () {
  initializeCharts();
  setupRefreshButton();
  setupBulkImportHandlers();
  setupExportButtons();
});

// ============================
// Charts Initialization
// ============================
function initializeCharts() {
  // Monthly consumption & revenue data
  const consumptionCtx = document.getElementById('consumptionChart').getContext('2d');
  const revenueCtx = document.getElementById('revenueChart').getContext('2d');
  const topConsumersCtx = document.getElementById('topConsumersChart').getContext('2d');
  const usageDistCtx = document.getElementById('usageDistributionChart').getContext('2d');

  // Get monthly data from window.analyticsData
  const monthlyWaterData = window.analyticsData?.monthlyWaterData || [];
  const monthlyRentData = window.analyticsData?.monthlyRentData || [];

  // Create a combined dataset with all months
  const allMonths = new Set();

  // Add months from water data
  monthlyWaterData.forEach(item => {
    const monthKey = `${item._id.year}-${String(item._id.month).padStart(2, '0')}`;
    allMonths.add(monthKey);
  });

  // Add months from rent data
  monthlyRentData.forEach(item => {
    const monthKey = `${item._id.year}-${String(item._id.month).padStart(2, '0')}`;
    allMonths.add(monthKey);
  });

  // Sort months chronologically
  const sortedMonths = Array.from(allMonths).sort();

  // Create month labels for display
  const monthLabels = sortedMonths.map(monthKey => {
    const [year, month] = monthKey.split('-');
    const date = new Date(parseInt(year), parseInt(month) - 1);
    return date.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
  });

  // Create consumption data
  const consumptionData = sortedMonths.map(monthKey => {
    const [year, month] = monthKey.split('-');
    const waterItem = monthlyWaterData.find(item =>
      item._id.year === parseInt(year) && item._id.month === parseInt(month)
    );
    return waterItem ? waterItem.consumption : 0;
  });

  // Create water revenue data
  const waterRevenueData = sortedMonths.map(monthKey => {
    const [year, month] = monthKey.split('-');
    const waterItem = monthlyWaterData.find(item =>
      item._id.year === parseInt(year) && item._id.month === parseInt(month)
    );
    return waterItem ? waterItem.revenue : 0;
  });

  // Create rent revenue data
  const rentRevenueData = sortedMonths.map(monthKey => {
    const [year, month] = monthKey.split('-');
    const rentItem = monthlyRentData.find(item =>
      item._id.year === parseInt(year) && item._id.month === parseInt(month)
    );
    return rentItem ? rentItem.revenue : 0;
  });

  // Consumption Chart
  charts.consumption = new Chart(consumptionCtx, {
    type: 'line',
    data: {
      labels: monthLabels,
      datasets: [{
        label: 'Water Consumption (m³)',
        data: consumptionData,
        backgroundColor: 'rgba(54, 162, 235, 0.1)',
        borderColor: 'rgba(54, 162, 235, 1)',
        borderWidth: 3,
        fill: true,
        tension: 0.4,
        pointBackgroundColor: 'rgba(54, 162, 235, 1)',
        pointBorderColor: '#fff',
        pointBorderWidth: 2,
        pointRadius: 6
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top' },
        tooltip: {
          mode: 'index',
          intersect: false,
          callbacks: {
            label: function(context) {
              return `${context.dataset.label}: ${context.parsed.y.toFixed(1)} m³`;
            }
          }
        }
      },
      scales: {
        y: {
          beginAtZero: true,
          grid: { color: 'rgba(0, 0, 0, 0.1)' },
          title: { display: true, text: 'Consumption (m³)' }
        },
        x: { grid: { color: 'rgba(0, 0, 0, 0.1)' } }
      },
      interaction: { intersect: false, mode: 'index' }
    }
  });

  // Revenue Chart - Dual Line Chart for Water and Rent
  charts.revenue = new Chart(revenueCtx, {
    type: 'line',
    data: {
      labels: monthLabels,
      datasets: [
        {
          label: 'Water Revenue (KES)',
          data: waterRevenueData,
          backgroundColor: 'rgba(54, 162, 235, 0.1)',
          borderColor: 'rgba(54, 162, 235, 1)',
          borderWidth: 3,
          fill: false,
          tension: 0.4,
          pointBackgroundColor: 'rgba(54, 162, 235, 1)',
          pointBorderColor: '#fff',
          pointBorderWidth: 2,
          pointRadius: 6
        },
        {
          label: 'Rent Revenue (KES)',
          data: rentRevenueData,
          backgroundColor: 'rgba(255, 99, 132, 0.1)',
          borderColor: 'rgba(255, 99, 132, 1)',
          borderWidth: 3,
          fill: false,
          tension: 0.4,
          pointBackgroundColor: 'rgba(255, 99, 132, 1)',
          pointBorderColor: '#fff',
          pointBorderWidth: 2,
          pointRadius: 6
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top' },
        tooltip: {
          mode: 'index',
          intersect: false,
          callbacks: {
            label: function(context) {
              return `${context.dataset.label}: KES ${context.parsed.y.toLocaleString()}`;
            }
          }
        }
      },
      scales: {
        y: {
          beginAtZero: true,
          grid: { color: 'rgba(0, 0, 0, 0.1)' },
          title: { display: true, text: 'Revenue (KES)' },
          ticks: {
            callback: function(value) {
              return 'KES ' + value.toLocaleString();
            }
          }
        },
        x: {
          grid: { color: 'rgba(0, 0, 0, 0.1)' },
          title: { display: true, text: 'Month' }
        }
      },
      interaction: { intersect: false, mode: 'index' }
    }
  });

  // Top Consumers Chart
  const tenantUsage = {};
  if (window.analyticsData?.readings) {
    window.analyticsData.readings.forEach(reading => {
      if (!tenantUsage[reading.tenantName]) tenantUsage[reading.tenantName] = 0;
      tenantUsage[reading.tenantName] += reading.usage;
    });
  }

  const sortedTenants = Object.entries(tenantUsage)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 10);

  charts.topConsumers = new Chart(topConsumersCtx, {
    type: 'doughnut',
    data: {
      labels: sortedTenants.map(([name]) => name),
      datasets: [{
        data: sortedTenants.map(([, usage]) => usage),
        backgroundColor: [
          '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0',
          '#9966FF', '#FF9F40', '#FF6384', '#C9CBCF',
          '#4BC0C0', '#FF6384'
        ],
        borderWidth: 2,
        borderColor: '#fff'
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'right',
          labels: { usePointStyle: true, padding: 20 }
        }
      }
    }
  });

  // Usage Distribution Chart
  const usageRanges = { '0-10 m³': 0, '11-20 m³': 0, '21-30 m³': 0, '31-50 m³': 0, '50+ m³': 0 };

  if (window.analyticsData?.readings) {
    window.analyticsData.readings.forEach(reading => {
      const usage = reading.usage;
      if (usage <= 10) usageRanges['0-10 m³']++;
      else if (usage <= 20) usageRanges['11-20 m³']++;
      else if (usage <= 30) usageRanges['21-30 m³']++;
      else if (usage <= 50) usageRanges['31-50 m³']++;
      else usageRanges['50+ m³']++;
    });
  }

  charts.usageDistribution = new Chart(usageDistCtx, {
    type: 'pie',
    data: {
      labels: Object.keys(usageRanges),
      datasets: [{
        data: Object.values(usageRanges),
        backgroundColor: [
          'rgba(255, 99, 132, 0.8)',
          'rgba(54, 162, 235, 0.8)',
          'rgba(255, 205, 86, 0.8)',
          'rgba(75, 192, 192, 0.8)',
          'rgba(153, 102, 255, 0.8)'
        ],
        borderWidth: 2,
        borderColor: '#fff'
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { padding: 20, usePointStyle: true } }
      }
    }
  });
}

// ============================
// Refresh Button
// ============================
function setupRefreshButton() {
  const btn = document.getElementById('refresh-data');
  if (!btn) return;
  btn.addEventListener('click', function () {
    this.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Refreshing...';
    setTimeout(() => location.reload(), 1000);
  });
}

// ============================
// Export / Report Buttons
// ============================
function setupExportButtons() {
  const exportBtn = document.getElementById('export-charts-btn');
  if (exportBtn) {
    exportBtn.addEventListener('click', () => {
      exportChart('consumptionChart', 'consumption-trends');
    });
  }

  const reportBtn = document.getElementById('generate-report-btn');
  if (reportBtn) {
    reportBtn.addEventListener('click', () => {
      window.open('/generate_analytics_report', '_blank');
    });
  }
}

function exportChart(chartId, filename) {
  const chart = charts[chartId.replace('Chart', '')];
  if (chart) {
    const url = chart.toBase64Image();
    const link = document.createElement('a');
    link.download = filename + '.png';
    link.href = url;
    link.click();
  }
}

// ============================
// Bulk Import Spinners
// ============================
function setupBulkImportHandlers() {
  const excelForm = document.getElementById("bulkReadingsExcelForm");
  const textForm = document.getElementById("bulkReadingsTextForm");
  const spinner = document.getElementById("bulkReadingsSpinner");

  if (excelForm) {
    excelForm.addEventListener("submit", () => {
      spinner.style.display = "block";
    });
  }

  if (textForm) {
    textForm.addEventListener("submit", () => {
      spinner.style.display = "block";
    });
  }

  const importForm = document.getElementById("importForm");
  const loadingSpinner = document.getElementById("loadingSpinner");
  if (importForm && loadingSpinner) {
    importForm.addEventListener("submit", () => {
      loadingSpinner.style.display = "block";
    });
  }
}
