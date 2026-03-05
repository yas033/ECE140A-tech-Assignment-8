import os
import json
import threading
import asyncio  
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import select
from pydantic import BaseModel, Field

import db
import models
from db import engine, Base, get_db, SessionLocal
from models import Device, Reading

# MQTT
import paho.mqtt.client as mqtt

app = FastAPI()
templates = Jinja2Templates(directory="templates")

Base.metadata.create_all(bind=engine)

MQTT_BROKER = os.getenv("MQTT_BROKER", "broker.emqx.io")
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "ece140a/ta7/yas033")

READINGS_TOPIC = f"{MQTT_TOPIC}/readings"
COMMAND_TOPIC = f"{MQTT_TOPIC}/command"

class WSManager:
    def __init__(self):
        self.clients: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.clients:
            self.clients.remove(ws)

    async def broadcast(self, payload: Dict[str, Any]):
        dead = []
        for ws in self.clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

ws_manager = WSManager()


class CommandIn(BaseModel):
    command: str

class ReadingIn(BaseModel):
    mac_address: str
    pixels: List[float] = Field(..., min_length=64, max_length=64)

    thermistor_temp: float = Field(..., alias="thermistor")

    prediction: str
    confidence: float

    class Config:
        populate_by_name = True

def _normalize_prediction(pred: str) -> str:
    p = pred.strip().upper()
    if p not in ["PRESENT", "EMPTY"]:
        raise HTTPException(status_code=422, detail="prediction must be PRESENT or EMPTY")
    return p

def _insert_reading(db: Session, payload: ReadingIn) -> Reading:
    if len(payload.pixels) != 64:
        raise HTTPException(status_code=422, detail="pixels must have exactly 64 floats")

    pred = _normalize_prediction(payload.prediction)

    device = db.scalar(select(Device).where(Device.mac_address == payload.mac_address))
    if device is None:
        device = Device(mac_address=payload.mac_address)
        db.add(device)
        db.flush()

    r = Reading(
        mac_address=payload.mac_address,
        device_id=device.id,
        thermistor_temp=float(payload.thermistor_temp),
        prediction=pred,
        confidence=float(payload.confidence),
        pixels=[float(x) for x in payload.pixels],
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r

# ---------- Frontend ----------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.websocket("/ws")
async def ws(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            await asyncio.sleep(60)
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)

_mqtt_pub_lock = threading.Lock()
_mqtt_pub_client: Optional[mqtt.Client] = None

def _get_pub_client() -> mqtt.Client:
    global _mqtt_pub_client
    with _mqtt_pub_lock:
        if _mqtt_pub_client is None:
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

            def _on_pub_connect(client, userdata, flags, reason_code, properties):
                print(f"[MQTT-PUB] connected reason_code={reason_code}")

            c.on_connect = _on_pub_connect
            c.connect(MQTT_BROKER, 1883, 60)
            c.loop_start()
            _mqtt_pub_client = c
        return _mqtt_pub_client

print("[DEBUG] COMMAND_TOPIC =", repr(COMMAND_TOPIC))

@app.post("/api/command")
def api_command(body: CommandIn):
    cmd = body.command.strip().lower()
    if cmd not in ["get_one", "start_continuous", "stop"]:
        raise HTTPException(status_code=400, detail="Unknown command")

    try:
        c = _get_pub_client()
        print(f"[MQTT] Publishing '{cmd}' to {COMMAND_TOPIC}")
        c.publish(COMMAND_TOPIC, cmd, qos=0, retain=False)
        info = c.publish(COMMAND_TOPIC, cmd, qos=0, retain=False)
        info.wait_for_publish(timeout=2)
        print(f"[MQTT-PUB] published mid={info.mid} rc={info.rc}")
    except Exception as e:
        print(f"[MQTT] Publish Error: {e}")

    return {"ok": True, "command": cmd}

@app.post("/api/readings")
async def create_reading(payload: ReadingIn, db: Session = Depends(get_db)):
    r = _insert_reading(db, payload)

    await ws_manager.broadcast({
        "type": "reading",
        "id": r.id,
        "mac_address": r.mac_address,
        "thermistor_temp": r.thermistor_temp,
        "prediction": r.prediction,
        "confidence": r.confidence,
        "pixels": r.pixels,
    })

    return {"id": r.id}

@app.get("/api/readings")
def list_readings(device_mac: Optional[str] = None, db: Session = Depends(get_db)):
    stmt = select(Reading).order_by(Reading.id.desc())
    if device_mac:
        stmt = stmt.where(Reading.mac_address == device_mac)
    rows = db.scalars(stmt).all()
    return [
        {
            "id": r.id,
            "mac_address": r.mac_address,
            "thermistor_temp": r.thermistor_temp,
            "prediction": r.prediction,
            "confidence": r.confidence,
            "pixels": r.pixels,
        }
        for r in rows
    ]

@app.delete("/api/readings/{reading_id}")
def delete_reading(reading_id: int, db: Session = Depends(get_db)):
    r = db.get(Reading, reading_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(r)
    db.commit()
    return {"ok": True}

@app.get("/api/devices")
def list_devices(db: Session = Depends(get_db)):
    devices = db.scalars(select(Device).order_by(Device.id.desc())).all()
    return [{"id": d.id, "mac_address": d.mac_address} for d in devices]

def _on_mqtt_connect(client, userdata, flags, reason_code, properties):
    print(f"[MQTT] Subscribed to {READINGS_TOPIC}")
    client.subscribe(READINGS_TOPIC, qos=0)

def _on_mqtt_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))
        payload = ReadingIn(**data)
    except Exception as e:
        print(f"[MQTT] Parse Error: {e}")
        return

    db = SessionLocal()
    try:
        r = _insert_reading(db, payload)
    except Exception as e:
        print(f"[DB] Insert Error: {e}")
        db.rollback()
        return
    finally:
        db.close()

    try:
        if hasattr(app.state, 'main_loop'):
            coro = ws_manager.broadcast({
                "type": "reading",
                "id": r.id,
                "mac_address": r.mac_address,
                "thermistor_temp": r.thermistor_temp,
                "prediction": r.prediction,
                "confidence": r.confidence,
                "pixels": r.pixels,
            })
            asyncio.run_coroutine_threadsafe(coro, app.state.main_loop)
    except Exception as e:
        print(f"[WS] Broadcast Error: {e}")

def _mqtt_thread():
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    c.on_connect = _on_mqtt_connect
    c.on_message = _on_mqtt_message
    c.connect(MQTT_BROKER, 1883, 60)
    c.loop_forever()

@app.on_event("startup")
async def startup_event():
    app.state.main_loop = asyncio.get_running_loop()
    t = threading.Thread(target=_mqtt_thread, daemon=True)
    t.start()