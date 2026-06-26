#!/usr/bin/env sh
set -e

host="$1"
port="${2:-5432}"
shift 2

echo "Waiting for postgres at $host:$port..."
until pg_isready -h "$host" -p "$port" -q; do
  sleep 1
done

echo "Postgres is up at $host:$port"
exec "$@"
