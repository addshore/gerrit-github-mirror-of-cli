#!/usr/bin/env python3
"""Exercise the bundled PHP image's safe Xdebug defaults on real Docker bridges.

Runs CLI PHP and FastCGI requests against disposable containers. A small DBGp
client stands in for an IDE and verifies an actual breakpoint. Requires Python 3,
a local Linux Docker daemon with bridge networking, and a cached PHP image.
No packages, public ports or IDE are needed.
"""
import argparse
import json
import socket
import statistics
import struct
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ET

DEFAULT_IMAGE = "docker-registry.wikimedia.org/dev/bookworm-php83-fpm:1.0.0"
SCRIPT = """<?php
$value = 40;
$value += 2;
echo json_encode([
    'value' => $value,
    'modes' => xdebug_info('mode'),
    'host' => ini_get('xdebug.client_host'),
    'start' => ini_get('xdebug.start_with_request'),
    'upon_error' => ini_get('xdebug.start_upon_error'),
    'debugger' => xdebug_is_debugger_active(),
    'profiler' => xdebug_get_profiler_filename(),
]) . "\\n";
"""


def docker(*args, **kwargs):
    return subprocess.run(
        ["docker", *args], check=True, capture_output=True, text=True,
        timeout=45, **kwargs
    ).stdout.strip()


def receive_exact(connection, count):
    result = b""
    while len(result) < count:
        chunk = connection.recv(count - len(result))
        if not chunk:
            raise EOFError("connection closed before complete protocol frame")
        result += chunk
    return result


class Debugger:
    """Accept Xdebug, set a breakpoint, resume, and let PHP finish normally."""

    def __init__(self, host):
        self.socket = socket.socket()
        self.socket.bind((host, 0))
        self.port = self.socket.getsockname()[1]
        self.socket.listen()
        self.socket.settimeout(0.1)
        self.sessions = []
        self.errors = []
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    @staticmethod
    def packet(connection):
        length = b""
        while not length.endswith(b"\x00"):
            length += receive_exact(connection, 1)
        data = receive_exact(connection, int(length[:-1]))
        assert receive_exact(connection, 1) == b"\x00"
        return ET.fromstring(data)

    def serve(self):
        while not self.stopping.is_set():
            try:
                connection, peer = self.socket.accept()
            except socket.timeout:
                continue
            with connection:
                connection.settimeout(10)
                try:
                    initial = self.packet(connection)
                    filename = initial.attrib["fileuri"]
                    connection.sendall(
                        f"breakpoint_set -i 1 -t line -f {filename} -n 3\x00".encode()
                    )
                    assert self.packet(connection).attrib.get("id")
                    connection.sendall(b"run -i 2\x00")
                    assert self.packet(connection).attrib["status"] == "break"
                    connection.sendall(b"run -i 3\x00")
                    assert self.packet(connection).attrib["status"] == "stopping"
                    connection.sendall(b"detach -i 4\x00")
                    self.packet(connection)
                    self.sessions.append(peer[0])
                except Exception as error:
                    self.errors.append(str(error))

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=12)
        self.socket.close()
        assert not self.thread.is_alive(), "debugger thread did not stop"
        assert not self.errors, self.errors


def record(kind, body):
    return struct.pack("!BBHHBB", 1, kind, 1, len(body), 0, 0) + body


def param_length(length):
    return bytes([length]) if length < 128 else struct.pack("!I", length | 0x80000000)


def request(port, query=""):
    parameters = {
        "SCRIPT_FILENAME": "/var/www/html/w/probe.php",
        "SCRIPT_NAME": "/probe.php",
        "REQUEST_METHOD": "GET",
        "QUERY_STRING": query,
        "SERVER_PROTOCOL": "HTTP/1.1",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "REMOTE_ADDR": "127.0.0.1",
        "GATEWAY_INTERFACE": "CGI/1.1",
        "REDIRECT_STATUS": "200",
    }
    encoded = b""
    for name, value in parameters.items():
        name, value = name.encode(), value.encode()
        encoded += param_length(len(name)) + param_length(len(value)) + name + value
    with socket.create_connection(("127.0.0.1", port), timeout=10) as connection:
        connection.sendall(
            record(1, struct.pack("!HB5x", 1, 0)) + record(4, encoded)
            + record(4, b"") + record(5, b"")
        )
        output, errors = b"", b""
        while True:
            _, kind, _, size, padding, _ = struct.unpack("!BBHHBB", receive_exact(connection, 8))
            body = receive_exact(connection, size)
            receive_exact(connection, padding)
            if kind == 6:
                output += body
            elif kind == 7:
                errors += body
            elif kind == 3:
                assert struct.unpack("!I", body[:4])[0] == 0, errors.decode()
                break
    assert not errors, errors.decode()
    return json.loads(output.split(b"\r\n\r\n", 1)[1])


def check(payload, debugging):
    assert payload["value"] == 42, payload
    assert payload["modes"] == ["debug"], payload
    assert payload["host"] == "xdebug://gateway", payload
    assert payload["start"] in ("default", "trigger"), payload
    assert payload["upon_error"] in ("default", "no", "0"), payload
    assert payload["debugger"] is debugging, payload
    assert payload["profiler"] is False, payload


def wait_for_fpm(port):
    deadline = time.monotonic() + 15
    while True:
        try:
            return request(port)
        except (OSError, EOFError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def exercise(image, network, name):
    gateway = json.loads(docker("network", "inspect", network))[0]["IPAM"]["Config"][0]["Gateway"]
    debugger = Debugger(gateway)
    created = False
    try:
        docker(
            "run", "-d", "--name", name, "--network", network,
            "--publish", "127.0.0.1::9000", "--entrypoint", "sh",
            "-e", "XDEBUG_MODE=debug",
            "-e", f"XDEBUG_CONFIG=client_host=xdebug://gateway client_port={debugger.port} log=/tmp/xdebug-validation.log",
            image, "-c", 'mkdir -p /var/www/html/w; exec php-fpm"$PHP_VERSION" --nodaemonize',
        )
        created = True
        docker("exec", "-i", name, "sh", "-c", "cat > /var/www/html/w/probe.php", input=SCRIPT)
        port = int(docker("port", name, "9000/tcp").rsplit(":", 1)[1])
        check(wait_for_fpm(port), False)
        for _ in range(20):
            check(request(port), False)
        check(json.loads(docker("exec", name, "php", "/var/www/html/w/probe.php")), False)
        assert not debugger.sessions and not debugger.errors, "untriggered requests contacted the debugger"
        print(f"PASS [{network}]: ordinary CLI/FPM requests do not connect or profile", flush=True)

        check(json.loads(docker("exec", "-e", "XDEBUG_TRIGGER=1", name, "php", "/var/www/html/w/probe.php")), True)
        check(request(port, "XDEBUG_TRIGGER=1"), True)
        # The application response can reach the caller just before DBGp detaches.
        deadline = time.monotonic() + 2
        while len(debugger.sessions) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(debugger.sessions) == 2 and not debugger.errors, (debugger.sessions, debugger.errors)
        print(f"PASS [{network}]: triggered CLI/FPM requests hit and resume a real DBGp breakpoint", flush=True)
        debugger.close()

        started = time.monotonic()
        check(request(port, "XDEBUG_TRIGGER=1"), False)
        elapsed = time.monotonic() - started
        assert elapsed < 5, f"request stalled without an IDE: {elapsed:.3f}s"
        print(f"PASS [{network}]: missing IDE does not fail a triggered request ({elapsed:.3f}s)", flush=True)
        check(json.loads(docker("exec", "-e", "XDEBUG_PROFILE=1", name, "php", "/var/www/html/w/probe.php")), False)
        check(request(port, "XDEBUG_PROFILE=1"), False)
        assert not docker("exec", name, "find", "/tmp", "-name", "cachegrind.out*")
        print(f"PASS [{network}]: profile trigger cannot enable profiling; no cachegrind files", flush=True)

        # Report overhead rather than assert a noisy wall-clock threshold in CI.
        benchmark = 'function inc($v) { return $v + 1; } $t=hrtime(true); for($i=0;$i<200000;$i++){inc($i);} echo (hrtime(true)-$t)/1e6;'
        times = {mode: [] for mode in ("off", "debug")}
        for _ in range(5):
            for mode in times:
                times[mode].append(float(docker("exec", "-e", f"XDEBUG_MODE={mode}", name, "php", "-r", benchmark)))
        print(f"BENCH [{network}]: untriggered 200k PHP calls, median ms: " + json.dumps({mode: round(statistics.median(samples), 3) for mode, samples in times.items()}), flush=True)
    finally:
        try:
            if not debugger.stopping.is_set():
                debugger.close()
        finally:
            if created:
                docker("rm", "-f", name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    args = parser.parse_args()
    token = uuid.uuid4().hex[:12]
    network = "mwcli-xdebug-" + token
    docker("network", "create", network)
    try:
        for index, bridge in enumerate(("bridge", network)):
            exercise(args.image, bridge, f"mwcli-xdebug-{token}-{index}")
    finally:
        docker("network", "rm", network)


if __name__ == "__main__":
    main()
