import base64
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_preview import create_app
from test_travel_image import encoded_png


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(), base_url="http://127.0.0.1:8765")
        self.headers = {
            "Origin": "http://127.0.0.1:8765",
            "Content-Type": "application/json",
            "X-Travel-Request": "generate",
            "Sec-Fetch-Site": "same-origin",
        }

    @patch("local_preview.generate_image")
    def test_blocks_cross_origin_host_abuse_and_nonstatic_input(self, generate):
        for headers in (
            {**self.headers, "Origin": "https://attacker.example"},
            {**self.headers, "Host": "attacker.example:8765"},
            {**self.headers, "Sec-Fetch-Site": "cross-site"},
            {key: value for key, value in self.headers.items() if key != "X-Travel-Request"},
        ):
            self.assertEqual(self.client.post("/api/generate", headers=headers, content="{}").status_code, 403)
        response = self.client.post("/api/generate", headers=self.headers, content='{"description":"override"}')
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/generate", headers=self.headers, content=" " * 1025)
        self.assertEqual(response.status_code, 413)
        generate.assert_not_called()

    @patch("local_preview.generate_image")
    def test_delivers_png_and_surfaces_generation_error(self, generate):
        from travel_image import FlowError

        image = base64.b64decode(encoded_png())
        generate.return_value = (image, "test-digest", "test-invocation")
        response = self.client.post("/api/generate", headers=self.headers, content="{}")
        self.assertEqual(response.content, image)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.headers["cache-control"], "no-store")
        generate.side_effect = FlowError("Deployed Foundry invocation failed (HTTP 502).")
        with self.assertLogs("travel_preview", level="ERROR"):
            response = self.client.post("/api/generate", headers=self.headers, content="{}")
        self.assertEqual(response.status_code, 502)
        self.assertIn("HTTP 502", response.json()["error"])

    def test_static_page_preserves_the_sample_itinerary(self):
        html = self.client.get("/").text
        itinerary = Path("sample-itinerary.txt").read_text(encoding="utf-8").strip()
        source = html.split('<script id="itinerary-source" type="text/plain">', 1)[1].split("</script>", 1)[0]
        self.assertEqual(itinerary, source.replace("\r\n", "\n").strip())
        self.assertIn('id="generate"', html)
        self.assertEqual(self.client.get("/", headers={"Host": "attacker.example"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
