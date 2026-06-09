FROM python:3.11

# Set timezone and env vars
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=UTC

WORKDIR /app

# Upgrade pip and show architecture
RUN uname -a && pip install --upgrade pip setuptools wheel

# Install python dependencies
COPY requirements.txt .
# If pip install fails, print the last 150 lines of the log so we can see the exact error!
RUN pip install --no-cache-dir -v -r requirements.txt > pip_build.log 2>&1 || (echo "PIP INSTALL FAILED. SHOWING LOGS:" && tail -n 150 pip_build.log && exit 1)

# Copy project files
COPY . .

# Default command (will be overridden by docker-compose for each service)
CMD ["python", "bot.py"]
