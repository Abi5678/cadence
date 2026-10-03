#!/usr/bin/env bash
# Private CA + server cert with the docker0 IP as IP SAN, for Hermes -> Cadence MCP (§7.6).
set -euo pipefail
BR=$(ip -4 -o addr show docker0 | awk '{print $4}' | cut -d/ -f1); echo "bridge: $BR"
D=${CADENCE_TLS_DIR:-$HOME/cadence-tls}; mkdir -p "$D" && cd "$D"
[ -f ca.pem ] || openssl req -x509 -newkey rsa:2048 -nodes -days 7 -subj "/CN=Cadence Hackathon CA" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" -keyout ca-key.pem -out ca.pem
openssl req -newkey rsa:2048 -nodes -subj "/CN=$BR" -keyout server-key.pem -out server.csr
printf 'subjectAltName=IP:%s\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nauthorityKeyIdentifier=keyid\nsubjectKeyIdentifier=hash\n' "$BR" > ext.cnf
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca-key.pem -CAcreateserial -days 7 -extfile ext.cnf -out server.pem
openssl verify -x509_strict -CAfile ca.pem server.pem && chmod 644 ca.pem server.pem && chmod 600 ./*-key.pem
# Shared secret for Hermes -> MCP. Stays in this folder (outside git).
[ -f mcp-token ] || (umask 077; openssl rand -hex 32 > mcp-token)
