"""Invoke the deployed Foundry agent and verify its real private image link."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from azure.identity import DefaultAzureCredential
import httpx

from travel_image import decode_image, upstream_error
import base64


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True, help="Real deployed Invocations protocol URL")
    parser.add_argument("--description", required=True)
    parser.add_argument("--storage-host", help="Expected private account blob hostname for URL mode")
    parser.add_argument("--output", help="Save a directly returned image here; bypasses Blob entirely")
    parser.add_argument("--result-file", help="Optional private local file for the real URL and evidence")
    args = parser.parse_args()
    if not args.output and not args.storage_host:
        parser.error("Provide --output for direct image mode, or --storage-host for URL mode.")
    if urlsplit(args.endpoint).scheme != "https":
        raise ValueError("The deployed Foundry endpoint must use HTTPS.")
    with DefaultAzureCredential() as credential:
        token = credential.get_token("https://ai.azure.com/.default").token
        with httpx.Client(timeout=600) as client:
            result = client.post(
                args.endpoint,
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "description": args.description,
                    "response_format": "image" if args.output else "url",
                },
            )
            if result.is_error:
                # The agent's error contract is public-safe and identifies the failed stage.
                body = result.json()
                raise RuntimeError(f"Foundry invocation HTTP {result.status_code}: {body.get('error', 'no agent error detail')}")
            if args.output:
                data, content_type, _ = decode_image(
                    base64.b64encode(result.content).decode("ascii")
                )
                if result.headers.get("content-type", "").split(";")[0] != content_type:
                    raise ValueError("Direct response has an incorrect image content type.")
                digest = hashlib.sha256(data).hexdigest()
                if result.headers.get("x-image-sha256") != digest:
                    raise ValueError("Direct image differs from the server-validated bytes.")
                Path(args.output).write_bytes(data)
                evidence = {
                    "status": "passed",
                    "flow": "Foundry invocation -> prompt -> MAI -> direct binary image",
                    "http_status": result.status_code,
                    "content_type": content_type,
                    "image_bytes": len(data),
                    "sha256": digest,
                    "output": args.output,
                    "invocation_id": result.headers.get("x-agent-invocation-id"),
                }
                if args.result_file:
                    Path(args.result_file).write_text(
                        json.dumps(evidence, indent=2), encoding="utf-8"
                    )
                print(json.dumps(evidence, indent=2))
                return
            output = result.json()
            parts = urlsplit(output["url"])
            if parts.scheme != "https" or parts.hostname != args.storage_host:
                raise ValueError("Agent returned an unexpected storage URL.")
            sas = parse_qs(parts.query)
            if sas.get("sp") != ["r"] or sas.get("spr") != ["https"]:
                raise ValueError("SAS must allow read only over HTTPS.")
            if not all(sas.get(key) for key in ("skoid", "sktid", "sig", "se")):
                raise ValueError("A user-delegation SAS with expiry is required.")
            expires = datetime.fromisoformat(output["expires_at"])
            now = datetime.now(timezone.utc)
            if expires.tzinfo is None or not now < expires <= now + timedelta(days=7):
                raise ValueError("Returned URL expiry must be within the next seven days.")
            signed_expiry = datetime.fromisoformat(sas["se"][0].replace("Z", "+00:00"))
            if signed_expiry.tzinfo is None or abs((signed_expiry - expires).total_seconds()) > 1:
                raise ValueError("SAS expiry differs from the returned expiration.")
            try:
                image = client.get(output["url"])
            except httpx.HTTPError as exc:
                raise upstream_error("Signed image retrieval", exc) from None
            if image.status_code != 200:
                raise RuntimeError(f"Signed image retrieval failed (HTTP {image.status_code}).")
            _, content_type, _ = decode_image(base64.b64encode(image.content).decode("ascii"))
            if image.headers.get("content-type", "").split(";")[0] != content_type:
                raise ValueError("Retrieved image content type is incorrect.")
            try:
                anonymous = client.get(parts._replace(query="").geturl())
            except httpx.HTTPError as exc:
                raise upstream_error("Unsigned privacy check", exc) from None
            if anonymous.status_code not in (403, 404):
                raise ValueError("Unsigned blob URL unexpectedly permits public access.")
            evidence = {
                "status": "passed",
                "flow": "Foundry invocation -> prompt -> MAI -> private Blob -> HTTP retrieval",
                "image_bytes": len(image.content),
                "sha256": hashlib.sha256(image.content).hexdigest(),
                "unsigned_status": anonymous.status_code,
                "expires_at": output["expires_at"],
            }
            if args.result_file:
                Path(args.result_file).write_text(
                    json.dumps({"result": output, "evidence": evidence}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
