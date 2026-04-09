# Use a slim python image
FROM python:3.11-slim AS builder

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
ENV PIP_DEFAULT_TIMEOUT=120
RUN pip install --no-cache-dir --retries=5 --user -r requirements.txt

# --- Final Image ---
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copy application code
COPY app ./app
COPY scripts ./scripts

# Create the output directory for generated files
RUN mkdir -p ./app/generated

# Generate Proto files during build
RUN python -m grpc_tools.protoc \
    -I./app/proto \
    --python_out=./app/generated \
    --grpc_python_out=./app/generated \
    ./app/proto/langgraph.proto

# Fix imports for the container environment
RUN sed -i 's/import langgraph_pb2/from app.generated import langgraph_pb2/g' ./app/generated/langgraph_pb2_grpc.py && \
    touch ./app/generated/__init__.py

# Expose the port
EXPOSE 50051

# Set environment variables
ENV SERVER_PORT=50051

# Run the application
CMD ["python", "-m", "app.main"]