document.addEventListener('DOMContentLoaded', function() {
  const tenantSelect = document.getElementById('tenant_id');
  if (tenantSelect) {
    tenantSelect.addEventListener('change', function() {
      const tenantId = this.value;
      if (!tenantId) {
        document.getElementById('previous_reading').value = '';
        return;
      }
      
      // Find the tenant's last reading from the recent readings table
      const recentReadings = document.querySelectorAll('.recent-readings-table tbody tr');
      let lastReading = null;
      
      recentReadings.forEach(row => {
        const tenantLink = row.querySelector('td:first-child a');
        if (tenantLink && tenantLink.href.endsWith(tenantId)) {
          const currentReading = row.querySelector('td:nth-child(3)').textContent;
          // Extract the numeric value from the string (e.g., "10.5 m³" -> 10.5)
          lastReading = parseFloat(currentReading.replace(' m³', ''));
        }
      });
      
      if (lastReading !== null) {
        document.getElementById('previous_reading').value = lastReading;
      }
    });
  }
});