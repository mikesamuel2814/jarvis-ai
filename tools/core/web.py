"""
Jarvis v3 Web Tools (8 tools)
Category: web | Rank: R0-R2 | Scope: READ
"""

import base64
import json
import socket
import ssl
import subprocess
from typing import Optional

import jwt
import requests
from bs4 import BeautifulSoup

from ..decorator import jarvis_tool
from ..result import ToolResult


@jarvis_tool(
    name="http_request",
    description="HTTP request using requests library",
    params={
        "url": {"type": "string", "required": True},
        "method": {"type": "string", "required": False, "default": "GET"},
        "headers": {"type": "object", "required": False, "default": {}},
        "body": {"type": "string", "required": False, "default": ""},
        "timeout": {"type": "integer", "required": False, "default": 30},
    },
    rank="R0", scope="READ", category="web", tags=["web", "http", "request", "api"]
)
def http_request(url: str, method: str = "GET", headers: Optional[dict] = None, body: str = "", timeout: int = 30) -> ToolResult:
    try:
        resp = requests.request(
            method.upper(),
            url,
            headers=headers or {},
            data=body if body else None,
            timeout=timeout,
        )
        data = {
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "url": resp.url,
        }
        output = f"Status: {resp.status_code}\n{resp.text[:2000]}"
        return ToolResult.ok(output=output, data=data, tool_name="http_request")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="http_request")


@jarvis_tool(
    name="api_test",
    description="Test API endpoint and verify expected status code",
    params={
        "url": {"type": "string", "required": True},
        "expected_status": {"type": "integer", "required": False, "default": 200},
        "method": {"type": "string", "required": False, "default": "GET"},
    },
    rank="R0", scope="READ", category="web", tags=["web", "api", "test"]
)
def api_test(url: str, expected_status: int = 200, method: str = "GET") -> ToolResult:
    try:
        resp = requests.request(method.upper(), url, timeout=30)
        passed = resp.status_code == expected_status
        data = {
            "status_code": resp.status_code,
            "expected": expected_status,
            "passed": passed,
            "response_time_ms": int(resp.elapsed.total_seconds() * 1000),
        }
        status = "PASS" if passed else "FAIL"
        output = f"{status} — Got {resp.status_code}, expected {expected_status}\n{resp.text[:1000]}"
        return ToolResult.ok(output=output, data=data, tool_name="api_test")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="api_test")


@jarvis_tool(
    name="web_scrape",
    description="Fetch and parse HTML",
    params={
        "url": {"type": "string", "required": True},
        "selector": {"type": "string", "required": False, "default": ""},
    },
    rank="R0", scope="READ", category="web", tags=["web", "scrape", "html", "parse"]
)
def web_scrape(url: str, selector: str = "") -> ToolResult:
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        if selector:
            elements = soup.select(selector)
            texts = [el.get_text(strip=True) for el in elements]
            output = "\n".join(texts[:50]) if texts else f"No elements matched selector: {selector}"
            data = {"matches": len(elements), "texts": texts[:50]}
        else:
            title = soup.title.string.strip() if soup.title else "No title"
            paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
            output = f"Title: {title}\n\n" + "\n".join(paragraphs[:20])
            data = {"title": title, "paragraphs": paragraphs[:20]}
        return ToolResult.ok(output=output, data=data, tool_name="web_scrape")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="web_scrape")


@jarvis_tool(
    name="jwt_decode",
    description="Decode JWT token without verification",
    params={"token": {"type": "string", "required": True}},
    rank="R0", scope="READ", category="web", tags=["web", "jwt", "token", "security"]
)
def jwt_decode(token: str) -> ToolResult:
    try:
        payload = jwt.decode(token, options={"verify_signature": False}, algorithms=["HS256", "RS256", "ES256"])
        output = json.dumps(payload, indent=2)
        return ToolResult.ok(output=output, data=payload, tool_name="jwt_decode")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="jwt_decode")


@jarvis_tool(
    name="base64_ops",
    description="Base64 encode or decode",
    params={
        "data": {"type": "string", "required": True},
        "operation": {"type": "string", "required": False, "default": "encode"},
    },
    rank="R0", scope="READ", category="web", tags=["web", "base64", "encode", "decode"]
)
def base64_ops(data: str, operation: str = "encode") -> ToolResult:
    try:
        op = operation.lower()
        if op == "encode":
            result = base64.b64encode(data.encode()).decode()
            output = f"Encoded:\n{result}"
        elif op == "decode":
            result = base64.b64decode(data).decode()
            output = f"Decoded:\n{result}"
        else:
            return ToolResult.fail(error="operation must be 'encode' or 'decode'", tool_name="base64_ops")
        return ToolResult.ok(output=output, data={"result": result}, tool_name="base64_ops")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="base64_ops")


@jarvis_tool(
    name="openssl_cert",
    description="Inspect SSL certificate",
    params={
        "host": {"type": "string", "required": True},
        "port": {"type": "integer", "required": False, "default": 443},
    },
    rank="R0", scope="READ", category="web", tags=["web", "ssl", "certificate", "tls"]
)
def openssl_cert(host: str, port: int = 443) -> ToolResult:
    try:
        result = subprocess.run(
            ["openssl", "s_client", "-connect", f"{host}:{port}", "-servername", host],
            input="",
            capture_output=True,
            text=True,
            timeout=15,
        )
        cert_text = ""
        if result.stdout:
            start = result.stdout.find("-----BEGIN CERTIFICATE-----")
            end = result.stdout.find("-----END CERTIFICATE-----")
            if start != -1 and end != -1:
                cert_pem = result.stdout[start:end + 25]
                p = subprocess.run(
                    ["openssl", "x509", "-noout", "-text"],
                    input=cert_pem,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                cert_text = p.stdout if p.returncode == 0 else result.stdout[:2000]
            else:
                cert_text = result.stdout[:2000]
        output = cert_text or result.stderr[:2000] or "No certificate data"
        return ToolResult.ok(output=output, tool_name="openssl_cert")
    except FileNotFoundError:
        return ToolResult.fail(error="openssl not found", tool_name="openssl_cert")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="openssl_cert")


@jarvis_tool(
    name="nginx_config",
    description="Test and show nginx configuration",
    rank="R2", scope="READ", category="web", tags=["web", "nginx", "config"]
)
def nginx_config() -> ToolResult:
    try:
        test = subprocess.run(["nginx", "-t"], capture_output=True, text=True, timeout=10)
        test_output = test.stdout + test.stderr
        try:
            result = subprocess.run(["nginx", "-T"], capture_output=True, text=True, timeout=10)
            full = result.stdout + result.stderr
        except Exception:
            full = ""
        output = f"Test result:\n{test_output}\n\nFull config:\n{full[:3000]}"
        data = {"test_returncode": test.returncode, "test_output": test_output, "config": full[:5000]}
        return ToolResult.ok(output=output, data=data, tool_name="nginx_config")
    except FileNotFoundError:
        return ToolResult.fail(error="nginx not found", tool_name="nginx_config")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="nginx_config")


@jarvis_tool(
    name="apache_status",
    description="Show Apache status",
    rank="R0", scope="READ", category="web", tags=["web", "apache", "status"]
)
def apache_status() -> ToolResult:
    try:
        output_parts = []
        for cmd in (["apache2ctl", "status"], ["apachectl", "status"], ["systemctl", "status", "apache2"]):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                output_parts.append(f"$ {' '.join(cmd)}\n{result.stdout}\n{result.stderr}")
                break
            except FileNotFoundError:
                continue
        if not output_parts:
            return ToolResult.fail(error="No apache control utility found", tool_name="apache_status")
        return ToolResult.ok(output="\n\n".join(output_parts), tool_name="apache_status")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="apache_status")
