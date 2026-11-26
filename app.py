"""FastAPI web application driving the Holmarc dual syringe pump.

Run with any of:

    python app.py                      # simplest; opens the browser
    start.bat                          # double-click launcher
    python -m uvicorn app:app --host 127.0.0.1 --port 8077
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pump import curves as curves_mod
from pump import device as device_mod
from pump.engine import controller
from pump.program import parse_program_text

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Dual Syringe Pump", version="2.0.0")


@app.middleware("http")
async def _no_cache_static(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class ConnectRequest(BaseModel):
    port: str


class PathRequest(BaseModel):
    path: str


class TextRequest(BaseModel):
    text: str
    name: str | None = None


class SelectedRequest(BaseModel):
    selected: list[bool]


class WithdrawalRequest(BaseModel):
    value: bool


class MarkHomeRequest(BaseModel):
    value: bool = True
    axis: str = "both"


class HomeRequest(BaseModel):
    target: Any = "both"


class PumpUpdateRequest(BaseModel):
    axis: str = "A"
    diameter: float | None = None
    flowRate: float | None = None
    flowRateUnit: int | None = None
    durationType: int | None = None
    duration: float | None = None
    durationUnit: int | None = None
    volumeUnit: int | None = None
    enabled: bool | None = None
    pumbStatus: bool | None = None
    withdrawal: bool | None = None


class ModeRequest(BaseModel):
    mode: str


class RunRequest(BaseModel):
    mode: str | None = None


class StepRequest(BaseModel):
    index: int
    diameter: float | None = None
    flowRate: float | None = None
    flowRateUnit: int | None = None
    durationType: int | None = None
    duration: float | None = None
    durationUnit: int | None = None
    volumeUnit: int | None = None
    withdrawal: bool | None = None
    pumbStatus: bool | None = None
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
def get_state() -> JSONResponse:
    return JSONResponse(controller.state())


@app.get("/api/ports")
def get_ports() -> JSONResponse:
    return JSONResponse({"ports": device_mod.list_ports()})


@app.post("/api/connect")
def connect(request: ConnectRequest) -> JSONResponse:
    try:
        controller.connect(request.port)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(controller.state())


@app.post("/api/disconnect")
def disconnect() -> JSONResponse:
    controller.disconnect()
    return JSONResponse(controller.state())


@app.post("/api/home")
def home(request: HomeRequest | None = None) -> JSONResponse:
    target = "both"
    if request:
        target = request.target
    try:
        controller.home(target)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(controller.state())


@app.post("/api/home-all")
def home_all() -> JSONResponse:
    try:
        controller.home_all()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(controller.state())


@app.post("/api/pump")
def update_pump(request: PumpUpdateRequest) -> JSONResponse:
    payload = request.model_dump(exclude_none=True, exclude={"axis"})
    try:
        controller.update_pump(request.axis, payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(controller.state())


@app.post("/api/step")
def update_step(request: StepRequest) -> JSONResponse:
    payload = request.model_dump(exclude_none=True, exclude={"index"})
    try:
        controller.update_step(request.index, payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(controller.state())


@app.post("/api/mode")
def set_mode(request: ModeRequest) -> JSONResponse:
    try:
        controller.set_mode(request.mode)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(controller.state())


@app.post("/api/run")
def run(request: RunRequest | None = None) -> JSONResponse:
    mode = request.mode if request else None
    try:
        controller.run(mode=mode)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(controller.state())


@app.post("/api/stop")
def stop() -> JSONResponse:
    controller.stop()
    return JSONResponse(controller.state())


@app.post("/api/emergency-stop")
def emergency_stop() -> JSONResponse:
    controller.emergency_stop()
    return JSONResponse(controller.state())


@app.post("/api/clear-fault")
def clear_fault() -> JSONResponse:
    controller.clear_fault()
    return JSONResponse(controller.state())


@app.post("/api/mark-home")
def mark_home(request: MarkHomeRequest) -> JSONResponse:
    controller.mark_at_home(request.value, request.axis)
    return JSONResponse(controller.state())


@app.post("/api/load")
def load(request: PathRequest) -> JSONResponse:
    try:
        controller.load_file(request.path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(controller.state())


@app.post("/api/load-text")
def load_text(request: TextRequest) -> JSONResponse:
    try:
        steps = parse_program_text(request.text)
        if not steps:
            raise ValueError("no program steps found in text")
        controller.set_steps(steps)
        controller.status = "Successfully Loaded"
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(controller.state())


@app.post("/api/save")
def save(request: PathRequest) -> JSONResponse:
    try:
        controller.save_file(request.path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(controller.state())


@app.post("/api/selected")
def selected(request: SelectedRequest) -> JSONResponse:
    try:
        controller.set_selected(request.selected)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(controller.state())


@app.post("/api/withdrawal")
def withdrawal(request: WithdrawalRequest) -> JSONResponse:
    try:
        controller.set_withdrawal(request.value)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(controller.state())


@app.post("/api/curve/preview")
def curve_preview(payload: dict[str, Any]) -> JSONResponse:
    try:
        compiled = curves_mod.compile_curve_config(payload)
        out = {k: v for k, v in compiled.items() if k != "_compiled_slices"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(out)


@app.post("/api/curve/config")
def curve_config(payload: dict[str, Any]) -> JSONResponse:
    try:
        preview = controller.set_curve_config(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"preview": preview, "state": controller.state()})


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _free_port(start: int, attempts: int = 20) -> int:
    import socket

    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def main() -> None:
    import os
    import threading
    import webbrowser

    import uvicorn

    start = int(os.environ.get("PUMP_PORT", "8077"))
    port = _free_port(start)
    if port != start:
        print(f"port {start} is busy; using {port} instead")
    url = f"http://127.0.0.1:{port}"
    print(f"\n  Dual Syringe Pump  ->  {url}\n  press Ctrl+C to stop\n")

    if os.environ.get("PUMP_NO_BROWSER") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
