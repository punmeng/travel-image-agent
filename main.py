"""Stateless Foundry Hosted Agent using the pass-through Invocations protocol."""

from contextlib import asynccontextmanager
import logging
import hashlib
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
import uvicorn

from travel_image import FlowError, GeneratedImage, Settings, TravelImageFlow


logger = logging.getLogger("travel_image_agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.flow = TravelImageFlow(Settings.from_environment())
    yield


app = FastAPI(title="Travel Image Agent", lifespan=lifespan)


class TravelRequest(BaseModel):
    description: str = Field(min_length=1, max_length=12000)
    response_format: Literal["url", "image"] = "url"


@app.get("/readiness")
def readiness():
    return {"status": "ready"}


@app.get("/liveness")
def liveness():
    return {"status": "alive"}


@app.post("/invocations", responses={200: {"content": {"image/png": {}, "image/jpeg": {}}}})
def invoke(body: TravelRequest, request: Request):
    invocation_id = uuid4().hex
    try:
        result = request.app.state.flow.run(
            body.description, direct_image=body.response_format == "image"
        )
    except FlowError as exc:
        logger.error("Invocation %s: %s", invocation_id, exc)
        return JSONResponse(
            status_code=502,
            content={"error": str(exc), "invocation_id": invocation_id},
        )
    if isinstance(result, GeneratedImage):
        return Response(
            content=result.data,
            media_type=result.content_type,
            headers={
                "x-agent-invocation-id": invocation_id,
                "x-image-sha256": hashlib.sha256(result.data).hexdigest(),
                "Content-Disposition": f'attachment; filename="travel-image.{result.extension}"',
                "Cache-Control": "no-store",
            },
        )
    return JSONResponse(
        content=result,
        headers={"x-agent-invocation-id": invocation_id},
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8088, access_log=False)
