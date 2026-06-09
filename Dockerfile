FROM python:3.11

# Set timezone and env vars
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=UTC

WORKDIR /app

# Upgrade pip
RUN pip install --upgrade pip setuptools wheel

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Default command (will be overridden by docker-compose for each service)
CMD ["python", "bot.py"]
