#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
npm install --omit=dev --no-fund --no-audit
echo "OK charts deps installed"
