# Use a base image with Python 3
FROM python:3.12.1-slim-bookworm

# Crie o diretório 'recordings' e defina as permissões apropriadas
RUN mkdir /app/recordings && chmod 777 /app/recordings

# Install system dependencies
RUN apt-get update && \
    apt-get install -y libgl1-mesa-dev ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /

# Update pip and setuptools
RUN pip install --upgrade pip setuptools

# Copy the dependencies file to the working directory
COPY requirements.txt .

# Install any dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the content of the local src directory to the working directory
COPY . .

# Expose the port the app runs on
EXPOSE 80

# Create a non-root user and switch to it
RUN adduser --disabled-password --gecos '' myuser
USER myuser

CMD ["gunicorn", "-b", "0.0.0.0:80", "app:app"]