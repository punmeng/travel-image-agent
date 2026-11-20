"""Loopback-only development UI; Azure CLI credentials stay in this process."""

import argparse
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit

from azure.core.exceptions import ClientAuthenticationError
from azure.identity import AzureCliCredential
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
import httpx
from starlette.concurrency import run_in_threadpool
import uvicorn

from travel_image import FlowError, MAX_IMAGE_BYTES, decode_image, upstream_error


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "travel-storyboard.png"
logger = logging.getLogger("travel_preview")


def generate_image() -> tuple[bytes, str, str]:
    description = (ROOT / "sample-itinerary.txt").read_text(encoding="utf-8").strip()
    if not description or len(description) > 12000:
        raise FlowError("The static itinerary is missing or exceeds 12000 characters.")
    endpoint = os.environ.get("FOUNDRY_AGENT_ENDPOINT", "").strip()
    tenant = os.environ.get("AZURE_TENANT_ID", "").strip()
    parts = urlsplit(endpoint)
    if not tenant or parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise FlowError("Set FOUNDRY_AGENT_ENDPOINT to the HTTPS Invocations URL and AZURE_TENANT_ID to your tenant.")
    try:
        with AzureCliCredential(tenant_id=tenant, process_timeout=60) as credential:
            token = credential.get_token("https://ai.azure.com/.default").token
            with httpx.Client(timeout=httpx.Timeout(600, connect=20)) as client:
                with client.stream(
                    "POST",
                    endpoint,
                    headers={"Authorization": f"Bearer {token}"},
                    json={"description": description, "response_format": "image"},
                ) as response:
                    if response.status_code != 200:
                        raise FlowError(
                            f"Deployed Foundry invocation failed (HTTP {response.status_code}). "
                            "Check agent access/status, then retry explicitly."
                        )
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        if len(data) + len(chunk) > MAX_IMAGE_BYTES:
                            raise FlowError("Foundry image exceeded the 30 MiB application limit.")
                        data.extend(chunk)
                    content_type = response.headers.get("content-type", "").split(";")[0]
                    expected_hash = response.headers.get("x-image-sha256")
                    invocation_id = response.headers.get("x-agent-invocation-id", "")
    except ClientAuthenticationError:
        raise FlowError(
            "Azure CLI authentication failed. Sign in to your configured "
            "tenant using the same AZURE_CONFIG_DIR, then try again."
        ) from None
    except httpx.HTTPError as exc:
        raise upstream_error("Deployed Foundry invocation", exc) from None
    image, actual_type, _ = decode_image(base64.b64encode(data).decode("ascii"))
    digest = hashlib.sha256(image).hexdigest()
    if content_type != actual_type or expected_hash != digest:
        raise FlowError("Returned image failed content-type or SHA-256 integrity verification.")
    if actual_type != "image/png":
        raise FlowError("This preview expects a PNG image; the agent returned another format.")
    OUTPUT.write_bytes(image)
    (ROOT / "web-preview-evidence.json").write_text(
        json.dumps({
            "flow": "browser button -> local bridge -> Foundry -> MAI -> PNG preview",
            "http_status": 200,
            "content_type": actual_type,
            "image_bytes": len(image),
            "sha256": digest,
            "invocation_id": invocation_id,
            "output": OUTPUT.name,
        }, indent=2),
        encoding="utf-8",
    )
    return image, digest, invocation_id


def create_app(port: int = 8765) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    expected_host = f"127.0.0.1:{port}"
    expected_origin = f"http://{expected_host}"
    generation_lock = threading.Lock()

    @app.middleware("http")
    async def protect_loopback(request: Request, call_next):
        if request.headers.get("host") != expected_host:
            return JSONResponse({"error": "Use the documented 127.0.0.1 preview URL."}, status_code=403)
        if request.method == "POST":
            if (
                request.headers.get("origin") != expected_origin
                or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
                or request.headers.get("x-travel-request") != "generate"
            ):
                return JSONResponse({"error": "Generation requires a same-origin page request."}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"error": "Send application/json."}, status_code=415)
            length = request.headers.get("content-length", "")
            if not length.isascii() or not length.isdigit() or len(length) > 4 or int(length) > 1024:
                return JSONResponse({"error": "A request size of at most 1024 bytes is required."}, status_code=413)
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' blob:; "
                "connect-src 'self'; object-src 'none'; base-uri 'none'; "
                "frame-ancestors 'none'; form-action 'none'"
            ),
        })
        return response

    @app.get("/")
    def index():
        return FileResponse(ROOT / "index.html", media_type="text/html")

    @app.get("/health")
    def health():
        return {"status": "ready", "binding": "loopback"}

    @app.post("/api/generate")
    async def generate(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 1024:
                return JSONResponse({"error": "Request exceeds 1024 bytes."}, status_code=413)
            body.extend(chunk)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return JSONResponse({"error": "Request must be the empty JSON object {}."}, status_code=400)
        if payload != {}:
            return JSONResponse({"error": "This preview uses the fixed sample itinerary; send {}."}, status_code=400)
        if not generation_lock.acquire(blocking=False):
            return JSONResponse({"error": "An image is already generating. Wait for it to finish."}, status_code=409)
        try:
            image, digest, invocation_id = await run_in_threadpool(generate_image)
            return Response(image, media_type="image/png", headers={
                "x-image-sha256": digest,
                "x-agent-invocation-id": invocation_id,
                "Content-Disposition": f'attachment; filename="{OUTPUT.name}"',
            })
        except FlowError as exc:
            logger.error("%s", exc)
            return JSONResponse({"error": str(exc)}, status_code=502)
        except OSError:
            logger.exception("Could not read the itinerary or save the generated PNG.")
            return JSONResponse({
                "error": "Could not read/save local files. Check folder permissions and try again."
            }, status_code=500)
        finally:
            generation_lock.release()

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    uvicorn.run(create_app(args.port), host="127.0.0.1", port=args.port, access_log=False)
