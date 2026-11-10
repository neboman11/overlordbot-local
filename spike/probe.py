"""Milestone-1 compatibility investigation; not a production radio client."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time

PASSWORD = secrets.token_urlsafe(24)


def receive_until(stream, kind):
    for _ in range(32):
        line = stream.readline(1024 * 1024)
        if not line:
            raise RuntimeError("SRS disconnected before expected response")
        message = json.loads(line)
        if message["MsgType"] == kind:
            return message
    raise RuntimeError("Expected response absent from 32 messages")


def authenticate(password_key, guid):
    client = {"ClientGuid": guid, "Name": "OverlordBot compatibility probe", "Coalition": 0}
    with socket.create_connection(("127.0.0.1", 5002), timeout=5) as connection:
        stream = connection.makefile("rb")
        def send(message):
            connection.sendall((json.dumps(message) + "\n").encode())
        send({"Client": client, "MsgType": 2, "Version": "1.9.0.0"})
        sync = receive_until(stream, 2)
        if sync["Version"] != "2.4.1.0":
            raise RuntimeError(f"Unexpected server version: {sync['Version']}")
        send({"Client": client, "MsgType": 7, "Version": "1.9.0.0", password_key: PASSWORD})
        auth = receive_until(stream, 7)
        return {"password_field": password_key, "client_version": "1.9.0.0",
                "server_version": sync["Version"], "sync_received": True,
                "authenticated_coalition": auth["Client"]["Coalition"]}

def main():
    environment = dict(os.environ, DOTNET_SYSTEM_GLOBALIZATION_INVARIANT="1",
                       DOTNET_BUNDLE_EXTRACT_BASE_DIR="/tmp/dotnet-bundle")
    with open("/tmp/srs-server.log", "w+") as log:
        server = subprocess.Popen(["/srs-server", "--port", "5002", "--enableEAM", "true",
                                   "--eamBluePassword", PASSWORD, "--coalitionSecurity", "true",
                                   "--clientExportPath", "/tmp/clients.json"],
                                  cwd="/tmp", env=environment, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 45
            while True:
                if server.poll() is not None:
                    raise RuntimeError(f"Server exited: {server.returncode}")
                try:
                    with socket.create_connection(("127.0.0.1", 5002), timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError("Server did not listen within 45 seconds")
                    time.sleep(0.1)
            original = authenticate("ExternalAwacsModePassword", "aaaaaaaaaaaaaaaaaaaaaa")
            current = authenticate("ExternalAWACSModePassword", "bbbbbbbbbbbbbbbbbbbbbb")
            result = {"original": original, "corrected_control": current,
                      "compatible": original["authenticated_coalition"] == 2,
                      "audio_round_trip": "not attempted: original EAM authentication rejected"}
            print(json.dumps(result, indent=2), flush=True)
            if original["authenticated_coalition"] != 0 or current["authenticated_coalition"] != 2:
                raise RuntimeError("Compatibility hypothesis not established")
            Path("/evidence/protocol.json").write_text(json.dumps(result, indent=2) + "\n")
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
            log.seek(0)
            print("--- SRS server log ---\n" + log.read(), flush=True)


if __name__ == "__main__":
    main()
