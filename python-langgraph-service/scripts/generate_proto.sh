#!/bin/bash

# Generate Python gRPC code from scripts files
cd "$(dirname "$0")"

echo "Generating Python gRPC code..."

python -m grpc_tools.protoc \
    -I./proto \
    --python_out=./app \
    --grpc_python_out=./app \
    ./proto/langgraph.proto

echo "Python gRPC code generated successfully!"
echo "Generated files:"
ls -la app/*pb2*.py 2>/dev/null || echo "No generated files found yet"
