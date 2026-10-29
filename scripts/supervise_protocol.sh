#!/usr/bin/env bash
set -u
cd /data/wengtengjin/tracememory-aml
while true; do
  OPENBLAS_NUM_THREADS=4 OMP_NUM_THREADS=4 .venv-candidate/bin/python -m system_service.protocol >> protocol-service.log 2>&1
  sleep 2
done
