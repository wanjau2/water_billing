import random
from faker import Faker

fake = Faker()

# Generate 100 rows of data
names = [fake.name() for _ in range(100)]
house_numbers = [f"{random.choice('ABCDEFGHJ')} {random.randint(1, 20)}".replace(" ", "") for _ in range(100)]
phones = [f"07{random.randint(0, 9)}{random.randint(1000000, 9999999)}" for _ in range(100)]
last_readings = [random.randint(800, 1600) for _ in range(100)]

# Create DataFrame
df_large = pd.DataFrame({
    "name": names,
    "house_number": house_numbers,
    "phone": phones,
    "last_reading": last_readings
})

# Save to Excel
large_file_path = "/mnt/data/sample_tenant_data_100.xlsx"
df_large.to_excel(large_file_path, index=False)

large_file_path
