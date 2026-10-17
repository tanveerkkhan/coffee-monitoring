#!/bin/bash
# Sends an order every 0.3s. Stop with Ctrl+C.
URL=${1:-http://localhost:5000}
while true; do
  curl -s -o /dev/null -w "%{http_code} " "$URL/order"
  sleep 0.3
done
