import base64
import hashlib
import html
import importlib.util
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import httpx

from test_travel_image import encoded_png
from travel_image import FlowError
import local_preview


ROOT = Path(__file__).resolve().parent
INTEGRATION = ROOT / "docs" / "integration"
spec = importlib.util.spec_from_file_location("public_client", INTEGRATION / "generate_travel_image.py")
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class PublicConfigurationTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_public_configuration_stops_before_authentication(self):
        with patch.object(client, "AzureCliCredential") as credential:
            with self.assertRaisesRegex(ValueError, "FOUNDRY_AGENT_ENDPOINT"):
                client.generate_image("Fictional city trip")
            with self.assertRaisesRegex(ValueError, "AZURE_TENANT_ID"):
                client.generate_image("Fictional city trip", endpoint="https://example.test/invocations")
            credential.assert_not_called()
        with patch.object(local_preview, "AzureCliCredential") as credential:
            with self.assertRaisesRegex(FlowError, "FOUNDRY_AGENT_ENDPOINT"):
                local_preview.generate_image()
            credential.assert_not_called()

    def test_standalone_client_uses_environment_and_retains_direct_contract(self):
        endpoint = "https://example.test/invocations?api-version=v1"
        image = base64.b64decode(encoded_png())
        digest = hashlib.sha256(image).hexdigest()

        def respond(request):
            self.assertEqual(str(request.url), endpoint)
            self.assertIn(b'"response_format":"image"', request.content)
            return httpx.Response(200, content=image, headers={
                "content-type": "image/png", "x-image-sha256": digest,
                "x-agent-invocation-id": "test-invocation",
            })

        real_client = httpx.Client
        with (
            patch.dict(os.environ, {
                "FOUNDRY_AGENT_ENDPOINT": endpoint, "AZURE_TENANT_ID": "test-tenant",
            }, clear=True),
            patch.object(client, "AzureCliCredential") as credential,
            patch.object(client.httpx, "Client", side_effect=lambda **kwargs: real_client(
                transport=httpx.MockTransport(respond), **kwargs
            )),
        ):
            credential.return_value.__enter__.return_value.get_token.return_value.token = "test-token"
            data, evidence = client.generate_image("Fictional city trip")
        credential.assert_called_once_with(tenant_id="test-tenant", process_timeout=60)
        self.assertEqual(data, image)
        self.assertEqual(evidence["sha256"], digest)

    def test_offline_downloads_and_displayed_sources_match_public_files(self):
        page = (INTEGRATION / "Foundry-agent-integration.html").read_text(encoding="utf-8")
        source = (INTEGRATION / "generate_travel_image.py").read_text(encoding="utf-8").strip()
        markdown = (INTEGRATION / "Foundry-agent-integration-complete.md").read_text(encoding="utf-8")
        for name in ("generate_travel_image.py", "requirements.txt"):
            match = re.search(
                rf'download="{re.escape(name)}" href="data:application/octet-stream;base64,([^"]+)"',
                page,
            )
            self.assertIsNotNone(match)
            self.assertEqual(base64.b64decode(match[1]), (INTEGRATION / name).read_bytes())
        self.assertIn(source, markdown)
        code = page.split('id="section-10"', 1)[1]
        self.assertIn(source, html.unescape(code))


if __name__ == "__main__":
    unittest.main()
