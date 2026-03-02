#!/usr/bin/env bash
# Send Python code to the Blender Bridge and print the result.
# Usage:
#   blender_exec.sh 'print(bpy.data.objects.keys())'
#   echo 'print(42)' | blender_exec.sh
#   blender_exec.sh -f script.py

set -euo pipefail

PORT="${BLENDER_BRIDGE_PORT:-9876}"
HOST="${BLENDER_BRIDGE_HOST:-localhost}"

# Gather code from argument, -f file, or stdin
if [[ $# -ge 2 && "$1" == "-f" ]]; then
    code="$(cat "$2")"
elif [[ $# -ge 1 && "$1" != "-" ]]; then
    code="$1"
else
    code="$(cat)"
fi

python -c "
import socket, struct, json, sys

code = sys.argv[1]
host, port = sys.argv[2], int(sys.argv[3])

s = socket.socket()
s.settimeout(10)
s.connect((host, port))

data = code.encode('utf-8')
s.sendall(struct.pack('>I', len(data)) + data)

hdr = b''
while len(hdr) < 4:
    hdr += s.recv(4 - len(hdr))
size = struct.unpack('>I', hdr)[0]

resp = b''
while len(resp) < size:
    resp += s.recv(size - len(resp))
s.close()

r = json.loads(resp)
if r.get('stdout'):
    print(r['stdout'], end='')
if r.get('stderr'):
    print(r['stderr'], end='', file=sys.stderr)
if r.get('error'):
    print(r['error'], end='', file=sys.stderr)
if not r.get('ok'):
    sys.exit(1)
" "$code" "$HOST" "$PORT"
