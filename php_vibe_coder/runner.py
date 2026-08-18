import atexit
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

class CodeIgniterPreviewServer:
    def __init__(self):
        self.process = None
        self.url = None
        atexit.register(self.stop)

    def start(self, workspace):
        self.stop()
        port = self.available_port()
        command = [
            "php",
            "spark",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]
        try:
            self.process = subprocess.Popen(
                command,
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        except OSError as error:
            return {
                "url": None,
                "error": str(error),
            }
        self.url = f"http://127.0.0.1:{port}"
        for unused_attempt in range(20):
            if self.process.poll() is not None:
                output = self.process.communicate()[0]
                self.process = None
                self.url = None
                return {
                    "url": None,
                    "error": "The preview server stopped unexpectedly.\n" + output,
                }
            try:
                response = urlopen(self.url, timeout=2)
                html = response.read().decode("utf-8", errors="replace")
                failure_words = ("Whoops!", "Fatal error", "Parse error", "DatabaseException")
                if response.status < 400 and not any(word in html for word in failure_words):
                    return {
                        "url": self.url,
                        "error": None,
                    }
                self.stop()
                return {
                    "url": None,
                    "error": "The generated preview returned an application error.",
                }
            except HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")
                self.stop()
                return {
                    "url": None,
                    "error": f"Preview request failed with HTTP {error.code}.\n{body}",
                }
            except URLError:
                time.sleep(0.5)
        self.stop()
        return {
            "url": None,
            "error": "The preview server did not start within 10 seconds.",
        }

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.communicate()
        self.process = None
        self.url = None

    def available_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
            server_socket.bind(("127.0.0.1", 0))
            return server_socket.getsockname()[1]

class CodeIgniterRunner:
    def run_command(self, command, workspace):
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

            return {
                "command": command,
                "return_code": completed.returncode,
                "output": completed.stdout + completed.stderr,
                "kind": "code",
            }
        except subprocess.TimeoutExpired:
            return {
                "command": command,
                "return_code": -1,
                "output": "The command timed out after 60 seconds.",
                "kind": "environment",
            }
        except OSError as error:
            return {
                "command": command,
                "return_code": -1,
                "output": str(error),
                "kind": "environment",
            }

    def check(self, workspace, generated_files=None):
        workspace = Path(workspace).resolve()
        errors = self.check_php_syntax(workspace, generated_files)
        if errors:
            return errors
        migration_error = self.check_migrations(workspace)
        if migration_error:
            return [migration_error]
        webpage_error = self.check_webpage(workspace)
        if webpage_error:
            return [webpage_error]
        return []

    def check_php_syntax(self, workspace, generated_files=None):
        errors = []
        if generated_files:
            paths = [Path(workspace) / filename for filename in generated_files]
        else:
            paths = workspace.joinpath("app").rglob("*.php")
        for path in paths:
            if path.suffix != ".php" or not path.is_file():
                continue
            result = self.run_command(["php", "-l", str(path)], workspace)
            if result["return_code"] != 0:
                result["kind"] = "code"
                errors.append(result)
        return errors

    def check_migrations(self, workspace):
        result = self.run_command(["php", "spark", "migrate"], workspace)
        environment_words = ("Unable to connect", "Access denied", "Connection refused", "Unknown database", "No such file or directory")
        code_words = ("Fatal error", "Parse error", "DatabaseException", "SQLSTATE[")
        environment_failure = any(word in result["output"] for word in environment_words)
        code_failure = any(word in result["output"] for word in code_words)
        if environment_failure:
            result["kind"] = "environment"
            return result
        if result["return_code"] != 0 or code_failure:
            result["kind"] = "code"
            return result
        return None

    def check_webpage(self, workspace):
        command = ["php", "spark", "serve", 
                   "--host", "127.0.0.1",
                   "--port", "8080"
        ]
        try:
            server = subprocess.Popen(
                command,
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        except OSError as error:
            return {
                "command": command,
                "return_code": -1,
                "output": str(error),
                "kind": "environment",
            }
        error = None
        page_loaded = False
        try:
            for unused_attempt in range(20):
                if server.poll() is not None:
                    error = {
                        "command": command,
                        "return_code": server.returncode,
                        "output": "The CodeIgniter server stopped unexpectedly.",
                        "kind": "code",
                    }
                    break
                try:
                    response = urlopen("http://127.0.0.1:8080", timeout=2)
                    html = response.read().decode("utf-8", errors="replace")
                    failure_words = ("Whoops!", "Fatal error", "Parse error", "DatabaseException")
                    if response.status >= 400:
                        error = {
                            "command": command,
                            "return_code": response.status,
                            "output": html,
                            "kind": "code",
                        }
                    elif any(word in html for word in failure_words):
                        error = {
                            "command": command,
                            "return_code": response.status,
                            "output": html,
                            "kind": "code",
                        }
                    else:
                        page_loaded = True
                    break
                except HTTPError as http_error:
                    body = http_error.read().decode("utf-8", errors="replace")
                    error = {
                        "command": command,
                        "return_code": http_error.code,
                        "output": body,
                        "kind": "code",
                    }
                    break
                except URLError:
                    time.sleep(0.5)
            if not page_loaded and error is None:
                error = {
                    "command": command,
                    "return_code": -1,
                    "output": "The webpage did not start within 10 seconds.",
                    "kind": "environment",
                }
        finally:
            if server.poll() is None:
                try:
                    os.killpg(os.getpgid(server.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                server_output = server.communicate(timeout=5)[0]
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(server.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                server_output = server.communicate()[0]
        if error:
            error["output"] += ("\n\nSERVER OUTPUT:\n" + server_output + "\n\nCODEIGNITER LOG:\n" + self.read_latest_log(workspace))
        return error

    def read_latest_log(self, workspace):
        log_directory = Path(workspace) / "writable" / "logs"
        if not log_directory.is_dir():
            return "No log directory was found."
        log_files = sorted(
            log_directory.glob("*.log"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not log_files:
            return "No CodeIgniter log was created."
        return log_files[0].read_text(encoding="utf-8", errors="replace")[-10000:]
