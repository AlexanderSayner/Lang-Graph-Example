#!/bin/bash

# Generate Python gRPC code
cd "$(dirname "$0")/.." # Go to project root

echo "Generating Python gRPC code..."

python3 -m grpc_tools.protoc \
    -I./proto \
    --python_out=./app/generated \
    --grpc_python_out=./app/generated \
    ./proto/langgraph.proto

sed -i 's/import langgraph_pb2/from . import langgraph_pb2/g' ./app/generated/langgraph_pb2_grpc.py

echo "Python gRPC code generated successfully!"
echo "Generated files:"
ls -la app/*pb2*.py 2>/dev/null || echo "No generated files found yet"
