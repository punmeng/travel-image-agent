"""Call the deployed Foundry travel agent and save its direct PNG response."""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import AzureCliCredential, ManagedIdentityCredential


SCOPE = "https://ai.azure.com/.default"
MAX_BYTES = 30 * 1024 * 1024


def generate_image(description, *, endpoint=None, auth="cli", client_id=None, tenant_id=None):
    """Return verified PNG bytes and evidence; never upload to Blob Storage."""
    if not description.strip() or len(description) > 12000:
        raise ValueError("Provide 1 to 12000 characters of itinerary text.")
    endpoint = endpoint or os.environ.get("FOUNDRY_AGENT_ENDPOINT", "").strip()
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ValueError("Set FOUNDRY_AGENT_ENDPOINT or --endpoint to your HTTPS Invocations URL.")
    if auth not in ("cli", "managed-identity"):
        raise ValueError("auth must be cli or managed-identity.")
    if client_id and auth != "managed-identity":
        raise ValueError("client_id applies only to managed-identity authentication.")
    tenant_id = tenant_id or os.environ.get("AZURE_TENANT_ID", "").strip()
    if auth == "cli" and not tenant_id:
        raise ValueError("Set AZURE_TENANT_ID or --tenant-id for Azure CLI authentication.")

    started = time.perf_counter()
    credential = (
        AzureCliCredential(tenant_id=tenant_id, process_timeout=60)
        if auth == "cli"
        else ManagedIdentityCredential(**({"client_id": client_id} if client_id else {}))
    )
    with credential, httpx.Client(timeout=httpx.Timeout(600, connect=20)) as client:
        token = credential.get_token(SCOPE).token
        with client.stream(
            "POST",
            endpoint,
            headers={"Authorization": f"Bearer {token}", "Accept": "image/png"},
            json={"description": description, "response_format": "image"},
        ) as response:
            response.raise_for_status()
            if response.headers.get("content-type", "").split(";")[0] != "image/png":
                raise ValueError("Expected image/png, not JSON or HTML.")
            image = bytearray()
            for chunk in response.iter_bytes():
                if len(image) + len(chunk) > MAX_BYTES:
                    raise ValueError("Response exceeds this client's 30 MiB limit.")
                image.extend(chunk)
            digest = hashlib.sha256(image).hexdigest()
            if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Response does not have a PNG signature.")
            if response.headers.get("x-image-sha256") != digest:
                raise ValueError("PNG integrity verification failed.")
            evidence = {
                "http_status": response.status_code,
                "content_type": "image/png",
                "image_bytes": len(image),
                "sha256": digest,
                "invocation_id": response.headers.get("x-agent-invocation-id"),
                "elapsed_seconds": round(time.perf_counter() - started, 2),
            }
    return bytes(image), evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="UTF-8 itinerary file")
    parser.add_argument("--output", type=Path, default=Path("travel-storyboard.png"))
    parser.add_argument("--auth", choices=("cli", "managed-identity"), default="cli")
    parser.add_argument("--client-id", help="User-assigned managed identity client ID")
    parser.add_argument("--endpoint", help="HTTPS Invocations URL; defaults to FOUNDRY_AGENT_ENDPOINT")
    parser.add_argument("--tenant-id", help="CLI tenant; defaults to AZURE_TENANT_ID")
    args = parser.parse_args()
    try:
        image, evidence = generate_image(
            args.input.read_text(encoding="utf-8-sig"),
            endpoint=args.endpoint,
            auth=args.auth,
            client_id=args.client_id,
            tenant_id=args.tenant_id,
        )
        args.output.write_bytes(image)
    except (ClientAuthenticationError, httpx.HTTPError, OSError, ValueError) as exc:
        print(f"Image generation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({**evidence, "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
