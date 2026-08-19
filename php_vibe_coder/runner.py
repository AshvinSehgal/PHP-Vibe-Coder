import atexit
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

FAILURE_WORDS = ("Whoops!", "Fatal error", "Parse error", "DatabaseException")

def available_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return server_socket.getsockname()[1]

def start_server(workspace):
    port = available_port()
    command = ["php", "spark", "serve", "--host", "127.0.0.1", "--port", str(port)]
    try:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        return process, f"http://127.0.0.1:{port}", command, None
    except OSError as error:
        return None, None, command, str(error)

def wait_for_server(process, url):
    for unused_attempt in range(20):
        if process.poll() is not None:
            return False
        try:
            urlopen(url, timeout=2).close()
            return True
        except HTTPError:
            return True
        except URLError:
            time.sleep(0.5)
    return False

def stop_server(process):
    if process is None:
        return ""
    if process.poll() is None:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        return process.communicate(timeout=5)[0]
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        return process.communicate()[0]

def request_page(url, timeout=5):
    try:
        with urlopen(url, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace"), None
    except HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace"), None
    except (URLError, ValueError) as error:
        return None, "", str(error)

def error_result(command, output, kind="code", return_code=-1):
    return {
        "command": command,
        "return_code": return_code,
        "output": output,
        "kind": kind,
    }

class CodeIgniterPreviewServer:
    def __init__(self):
        self.process = None
        self.url = None
        atexit.register(self.stop)

    def start(self, workspace):
        self.stop()
        self.process, self.url, _, start_error = start_server(workspace)
        if start_error:
            return {"url": None, "error": start_error}
        if not wait_for_server(self.process, self.url):
            output = stop_server(self.process)
            self.process = None
            self.url = None
            return {"url": None, "error": "The preview server did not start.\n" + output}
        status, body, request_error = request_page(self.url)
        if request_error or status >= 400 or any(word in body for word in FAILURE_WORDS):
            self.stop()
            return {"url": None, "error": request_error or "The generated preview returned an application error."}
        return {"url": self.url, "error": None}

    def stop(self):
        stop_server(self.process)
        self.process = None
        self.url = None

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
            return error_result(command, completed.stdout + completed.stderr, return_code=completed.returncode)
        except subprocess.TimeoutExpired:
            return error_result(command, "The command timed out after 60 seconds.", "environment")
        except OSError as error:
            return error_result(command, str(error), "environment")

    def check(self, workspace, generated_files=None):
        workspace = Path(workspace).resolve()
        errors = self.check_php_syntax(workspace, generated_files)
        if errors:
            return errors
        error = self.check_migrations(workspace)
        if error:
            return [error]
        error = self.check_webpage(workspace)
        return [error] if error else []

    def check_php_syntax(self, workspace, generated_files=None):
        if generated_files:
            paths = [Path(workspace) / filename for filename in generated_files]
        else:
            paths = Path(workspace).joinpath("app").rglob("*.php")
        errors = []
        for path in paths:
            if path.suffix != ".php" or not path.is_file():
                continue
            result = self.run_command(["php", "-l", str(path)], workspace)
            if result["return_code"] != 0:
                errors.append(result)
        return errors

    def check_migrations(self, workspace):
        result = self.run_command(["php", "spark", "migrate"], workspace)
        environment_words = ("Unable to connect", "Access denied", "Connection refused", "Unknown database", "No such file")
        if any(word in result["output"] for word in environment_words):
            result["kind"] = "environment"
            return result
        code_words = ("Fatal error", "Parse error", "DatabaseException", "SQLSTATE[")
        if result["return_code"] != 0 or any(word in result["output"] for word in code_words):
            return result
        return None

    def check_webpage(self, workspace):
        process, url, command, start_error = start_server(workspace)
        if start_error:
            return error_result(command, start_error, "environment")
        if not wait_for_server(process, url):
            stopped_early = process.poll() is not None
            output = stop_server(process)
            kind = "code" if stopped_early else "environment"
            return error_result(command, "The webpage did not start.\n" + output, kind)
        status, body, request_error = request_page(url)
        output = stop_server(process)
        if request_error:
            return error_result(command, request_error, "environment")
        if status >= 400 or any(word in body for word in FAILURE_WORDS):
            details = body + "\n\nSERVER OUTPUT:\n" + output + "\n\nCODEIGNITER LOG:\n" + self.read_latest_log(workspace)
            return error_result(command, details, return_code=status)
        return None

    def run_application_tests(self, workspace):
        test_path = Path(workspace) / "tests" / "application_tests.json"
        try:
            tests = json.loads(test_path.read_text(encoding="utf-8"))["tests"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            return [], [error_result(["test-definition", "tests/application_tests.json"], str(error))]
        results = []
        routes = self.run_command(["php", "spark", "routes"], workspace)
        route_passed = routes["return_code"] == 0 and "GET" in routes["output"]
        results.append({"name": "Framework: routes", "passed": route_passed, "output": routes["output"]})
        if not route_passed:
            return results, [routes]
        missing_assets = [
            filename for filename in ("public/css/app.css", "public/js/app.js")
            if not Path(workspace).joinpath(filename).is_file()
        ]
        asset_output = "CSS and JavaScript files exist." if not missing_assets else "Missing: " + ", ".join(missing_assets)
        results.append({"name": "Framework: frontend files", "passed": not missing_assets, "output": asset_output})
        if missing_assets:
            return results, [error_result(["application-test", "frontend files"], asset_output)]
        test_results, test_errors = self.run_http_tests(workspace, tests)
        return results + test_results, test_errors

    def run_http_tests(self, workspace, tests):
        process, url, command, start_error = start_server(workspace)
        if start_error:
            return [], [error_result(command, start_error, "environment")]
        if not wait_for_server(process, url):
            output = stop_server(process)
            return [], [error_result(command, "The application test server did not start.\n" + output, "environment")]
        results = []
        errors = []
        for test in tests:
            result, error = self.run_http_test(url, test)
            results.append(result)
            if error:
                errors.append(error)
        output = stop_server(process)
        if errors and output:
            errors[-1]["output"] += "\n\nSERVER OUTPUT:\n" + output
        return results, errors

    def run_http_test(self, url, test):
        status, body, request_error = request_page(url + test["path"])
        missing = [text for text in test["contains"] if text.lower() not in body.lower()]
        has_error = any(word in body for word in FAILURE_WORDS)
        passed = not request_error and status == test["expected_status"] and not missing and not has_error
        lines = [
            f"GET {test['path']}",
            f"Expected status: {test['expected_status']}",
            f"Actual status: {status}",
        ]
        if missing:
            lines.append("Missing text: " + ", ".join(missing))
        if has_error:
            lines.append("The response contained a PHP or CodeIgniter error.")
        if request_error:
            lines.append("Request error: " + request_error)
        if passed:
            lines.append("All expected text was found.")
        output = "\n".join(lines)
        result = {"name": "Application: " + test["name"], "passed": passed, "output": output}
        if passed:
            return result, None
        command = ["application-test", test["name"], test["path"]]
        return result, error_result(command, output + "\n\nRESPONSE PREVIEW:\n" + body[:4000], return_code=status or -1)

    def read_latest_log(self, workspace):
        log_directory = Path(workspace) / "writable" / "logs"
        if not log_directory.is_dir():
            return "No log directory was found."
        log_files = sorted(log_directory.glob("*.log"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not log_files:
            return "No CodeIgniter log was created."
        return log_files[0].read_text(encoding="utf-8", errors="replace")[-10000:]
