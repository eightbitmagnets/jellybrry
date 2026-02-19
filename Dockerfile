FROM python:3.9-slim

ARG VERSION
ENV VERSION=$VERSION

WORKDIR /app

# Copy requirements first for cache efficiency
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app code
COPY app.py .
COPY static/ static/
COPY templates/ templates/

# Create config directory
RUN mkdir -p /config

# Expose port
EXPOSE 6070

# Run Flask
CMD ["python", "-u", "app.py"]