#!/bin/bash

# Generate Python gRPC code
cd "$(dirname "$0")/.." # Go to project root

echo "Generating Python gRPC code..."

# 1. Generate into app/generated
python -m grpc_tools.protoc \
    -I./proto \
    --python_out=./app/generated \
    --grpc_python_out=./app/generated \
    ./proto/langgraph.proto

# 2. Fix the import path for Python package compatibility
# The generated file has 'import langgraph_pb2', we change it to 'from . import langgraph_pb2'
# This works on Linux/Mac/WSL
sed -i 's/import langgraph_pb2/from . import langgraph_pb2/g' ./app/generated/langgraph_pb2_grpc.py

echo "Python gRPC code generated successfully!"
echo "Generated files:"
ls -la app/*pb2*.py 2>/dev/null || echo "No generated files found yet"
