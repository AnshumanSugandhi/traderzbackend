# Use official lightweight Python image
FROM python:3.10-slim

# 1. Install Tesseract OCR Linux Engine!
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# 2. Set working directory
WORKDIR /app

# 3. Copy dependencies and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy your actual project files (including category_master.csv!)
COPY . .

# 5. Expose the port Render uses
EXPOSE 8000

# 6. Run the server using Gunicorn
# IMPORTANT: Change "myproject" to whatever your main Django folder is named!
CMD ["gunicorn", "core.wsgi:application", "--bind", "0.0.0.0:8000"]