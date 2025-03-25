#!/bin/bash

if [[ -z "${SECRET_KEY}" ]]; then
  echo "Secret key missing"
else
  sed "s/SECRET_KEY_PLACEHOLDER/${SECRET_KEY}/g" /app/config.vanilla.py > /app/config.py
fi

/entrypoint.sh
