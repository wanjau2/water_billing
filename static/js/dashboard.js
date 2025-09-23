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

  // These objects will be populated by Jinja during rendering
  const monthlyData = {};
  const monthlyRevenue = {};
  let readingDate, monthYear;

  {% for reading, tenant in readings %}
    readingDate = new Date('{{ reading.date_recorded.strftime("%Y-%m-%d") }}');
    monthYear = readingDate.toLocaleString('default', { month: 'short', year: 'numeric' });

    if (!monthlyData[monthYear]) {
      monthlyData[monthYear] = 0;
      monthlyRevenue[monthYear] = 0;
    }
    monthlyData[monthYear] += {{ reading.usage }};
    monthlyRevenue[monthYear] += {{ reading.bill_amount }};
  {% endfor %}

  const months = Object.keys(monthlyData).sort((a, b) => {
    const dateA = new Date(a);
    const dateB = new Date(b);
    return dateA - dateB;
  });

  const monthlyUsage = months.map(month => monthlyData[month]);
  const monthlyRevenueData = months.map(month => monthlyRevenue[month]);

  // Consumption Chart
  charts.consumption = new Chart(consumptionCtx, {
    type: 'line',
    data: {
      labels: months,
      datasets: [{
        label: 'Water Consumption (m³)',
        data: monthlyUsage,
        backgroundColor: 'rgba(102, 126, 234, 0.1)',
        borderColor: 'rgba(102, 126, 234, 1)',
        borderWidth: 3,
        fill: true,
        tension: 0.4
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: true, position: 'top' } },
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

  // Revenue Chart
  charts.revenue = new Chart(revenueCtx, {
    type: 'bar',
    data: {
      labels: months,
      datasets: [{
        label: 'Monthly Revenue (KES)',
        data: monthlyRevenueData,
        backgroundColor: 'rgba(40, 167, 69, 0.8)',
        borderColor: 'rgba(40, 167, 69, 1)',
        borderWidth: 2,
        borderRadius: 8
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: true, position: 'top' } },
      scales: {
        y: {
          beginAtZero: true,
          grid: { color: 'rgba(0, 0, 0, 0.1)' },
          title: { display: true, text: 'Revenue (KES)' }
        },
        x: { grid: { display: false } }
      }
    }
  });

  // Top Consumers Chart
  const tenantUsage = {};
  {% for reading, tenant in readings %}
    if (!tenantUsage['{{ tenant.name }}']) tenantUsage['{{ tenant.name }}'] = 0;
    tenantUsage['{{ tenant.name }}'] += {{ reading.usage }};
  {% endfor %}

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

  {% for reading, tenant in readings %}
    (function () {
      const usage = {{ reading.usage }};
      if (usage <= 10) usageRanges['0-10 m³']++;
      else if (usage <= 20) usageRanges['11-20 m³']++;
      else if (usage <= 30) usageRanges['21-30 m³']++;
      else if (usage <= 50) usageRanges['31-50 m³']++;
      else usageRanges['50+ m³']++;
    })();
  {% endfor %}

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
