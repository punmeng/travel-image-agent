import base64
from io import BytesIO
import unittest
from unittest.mock import patch

import httpx
from PIL import Image
from fastapi.testclient import TestClient

from travel_image import FlowError, GeneratedImage, Settings, TravelImageFlow, decode_image, upstream_error
from main import app


def encoded_png(size=(1024, 1024)):
    stream = BytesIO()
    Image.new("RGB", size, "blue").save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode("ascii")


class ImageValidationTests(unittest.TestCase):
    def test_upstream_diagnostic_never_echoes_raw_error(self):
        response = httpx.Response(
            401,
            request=httpx.Request("POST", "https://example.test"),
            json={"error": {"code": "PermissionDenied", "message": "principal secret-token lacks permission"}},
            headers={"apim-request-id": "12345678-1234-1234-1234-123456789abc"},
        )
        error = upstream_error("MAI image generation", httpx.HTTPStatusError(
            "secret header", request=response.request, response=response
        ))
        self.assertIn("[PermissionDenied]", str(error))
        self.assertIn("diagnostic=permission", str(error))
        self.assertNotIn("secret", str(error))

    def test_valid_image_retains_original_bytes(self):
        encoded = encoded_png()
        data, content_type, extension = decode_image(encoded)
        self.assertEqual(data, base64.b64decode(encoded))
        self.assertEqual((content_type, extension), ("image/png", "png"))

    def test_invalid_base64_corruption_and_dimensions_are_rejected(self):
        for encoded in (
            None,
            "not base64!",
            base64.b64encode(b"not an image").decode("ascii"),
            encoded_png((8, 8)),
            encoded_png()[:-16],
        ):
            with self.subTest(encoded_type=type(encoded).__name__):
                with self.assertRaises(FlowError):
                    decode_image(encoded)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            "https://example.services.ai.azure.com/api/projects/test",
            "text-model",
            "https://example.services.ai.azure.com/mai/v1/images/generations",
            "https://example.blob.core.windows.net",
            "travel-images",
            24,
        )

    @patch("travel_image.generate_blob_sas")
    @patch("travel_image.BlobServiceClient")
    @patch("travel_image.httpx.Client")
    @patch("travel_image.AIProjectClient")
    @patch("travel_image.DefaultAzureCredential")
    def test_private_upload_has_exact_mai_contract_and_read_only_delegation(
        self, credential, project, http, storage, sas
    ):
        text = project.return_value.__enter__.return_value.get_openai_client.return_value
        response = text.__enter__.return_value.responses.create.return_value
        response.status = "completed"
        response.output_text = "Kyoto cherry blossoms beside a temple, soft spring light."
        image_response = http.return_value.__enter__.return_value.post.return_value
        image_response.json.return_value = {"data": [{"b64_json": encoded_png()}]}
        service = storage.return_value.__enter__.return_value
        service.account_name = "example"
        container = service.get_container_client.return_value
        container.get_container_properties.return_value = {"public_access": None}
        container.get_blob_client.return_value.url = "https://example.blob.core.windows.net/travel-images/test.png"
        sas.return_value = "test-signature"
        result = TravelImageFlow(self.settings).run("Kyoto in spring")
        call = http.return_value.__enter__.return_value.post.call_args
        self.assertEqual(call.kwargs["json"], {
            "model": "MAI-Image-2.7-Flash",
            "width": 1024,
            "height": 1024,
            "prompt": response.output_text,
        })
        self.assertEqual(str(sas.call_args.kwargs["permission"]), "r")
        self.assertEqual(sas.call_args.kwargs["protocol"], "https")
        self.assertIs(
            sas.call_args.kwargs["user_delegation_key"],
            service.get_user_delegation_key.return_value,
        )
        upload = container.get_blob_client.return_value.upload_blob.call_args
        self.assertFalse(upload.kwargs["overwrite"])
        self.assertEqual(upload.kwargs["content_settings"].content_type, "image/png")
        self.assertEqual(result["url"], container.get_blob_client.return_value.url + "?test-signature")

    def test_empty_input_does_not_authenticate_or_generate(self):
        with patch("travel_image.DefaultAzureCredential") as credential:
            with self.assertRaises(FlowError):
                TravelImageFlow(self.settings).run("  ")
            credential.assert_not_called()

    @patch("travel_image.BlobServiceClient")
    @patch("travel_image.httpx.Client")
    @patch("travel_image.AIProjectClient")
    @patch("travel_image.DefaultAzureCredential")
    def test_direct_image_never_touches_storage(self, credential, project, http, storage):
        response = (
            project.return_value.__enter__.return_value.get_openai_client
            .return_value.__enter__.return_value.responses.create.return_value
        )
        response.status = "completed"
        response.output_text = "A snowy Hokkaido winter scene."
        encoded = encoded_png()
        http.return_value.__enter__.return_value.post.return_value.json.return_value = {
            "data": [{"b64_json": encoded}]
        }
        no_storage = Settings(
            self.settings.project_endpoint,
            self.settings.text_deployment,
            self.settings.image_endpoint,
            "",
            "",
            24,
        )
        result = TravelImageFlow(no_storage).run("Hokkaido in February", direct_image=True)
        self.assertIsInstance(result, GeneratedImage)
        self.assertEqual(result.data, base64.b64decode(encoded))
        storage.assert_not_called()
        with self.assertRaisesRegex(FlowError, "URL mode requires"):
            TravelImageFlow(no_storage).run("Hokkaido in February")

    @patch("travel_image.AIProjectClient")
    @patch("travel_image.BlobServiceClient")
    @patch("travel_image.DefaultAzureCredential")
    def test_storage_failures_stop_before_chargeable_generation(
        self, credential, storage, project
    ):
        service = storage.return_value.__enter__.return_value
        container = service.get_container_client.return_value
        container.get_container_properties.return_value = {"public_access": "blob"}
        with self.assertRaisesRegex(FlowError, "must be private"):
            TravelImageFlow(self.settings).run("Kyoto in spring")
        project.assert_not_called()
        service.get_user_delegation_key.assert_not_called()
        container.get_container_properties.return_value = {"public_access": None}
        service.get_user_delegation_key.side_effect = PermissionError("secret diagnostic")
        with self.assertRaisesRegex(FlowError, "user-delegation permission") as error:
            TravelImageFlow(self.settings).run("Kyoto in spring")
        self.assertNotIn("secret diagnostic", str(error.exception))
        project.assert_not_called()

    def test_handler_delivers_binary_image_without_json_or_base64_wrapper(self):
        image_bytes = base64.b64decode(encoded_png())
        with (
            patch("main.Settings.from_environment", return_value=self.settings),
            patch("main.TravelImageFlow.run", return_value=GeneratedImage(
                image_bytes, "image/png", "png"
            )) as run,
            TestClient(app) as client,
        ):
            response = client.post("/invocations", json={
                "description": "Hokkaido in February",
                "response_format": "image",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.content, image_bytes)
        run.assert_called_once_with("Hokkaido in February", direct_image=True)


if __name__ == "__main__":
    unittest.main()
