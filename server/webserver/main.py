import os
import json
import threading
import asyncio  
from typing import List, Optional, Dict, Any

from models import Device, Reading, User, SessionToken


import bcrypt
import uuid

from fastapi import FastAPI, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, Response

from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import select
from pydantic import BaseModel, Field

import db
#import models
from db import engine, Base, get_db, SessionLocal
#from models import Device, Reading
from models import Device, Reading, User, SessionToken

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

class AuthIn(BaseModel):
    username: str
    password: str

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

#--------------------------session helper-----------------------------------------
def get_current_user_from_request(request: Request, db: Session) -> Optional[User]:
    token = request.cookies.get("session_token")
    if not token:
        return None

    session_row = db.scalar(
        select(SessionToken).where(SessionToken.session_token == token)
    )
    if session_row is None:
        return None

    user = db.get(User, session_row.user_id)
    return user


def require_user(
    request: Request,
    db: Session = Depends(get_db)
) -> User:
    user = get_current_user_from_request(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user

# ---------- Frontend ----------
#@app.get("/", response_class=HTMLResponse)
#def index(request: Request):
#    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_request(request, db)
    if user is None:
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "username": user.username
        }
    )

# ---------- Login----------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

# ---------- Register ----------
@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


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
def api_command(
    body: CommandIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
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
def list_readings(
    request: Request,
    device_mac: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
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
def delete_reading(
    reading_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    r = db.get(Reading, reading_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(r)
    db.commit()
    return {"ok": True}

@app.post("/api/register")
def api_register(body: AuthIn, db: Session = Depends(get_db)):

    username = body.username.strip()
    password = body.password

    if not username or not password:
        raise HTTPException(status_code=400, detail="Missing username or password")

    existing = db.scalar(select(User).where(User.username == username))
    if existing:
        raise HTTPException(status_code=409, detail="Username already exists")

    password_hash = bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt()
    ).decode("utf-8")

    user = User(
        username=username,
        password_hash=password_hash
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "ok": True,
        "id": user.id
    }

@app.post("/api/login")
def api_login(body: AuthIn, response: Response, db: Session = Depends(get_db)):

    username = body.username.strip()
    password = body.password

    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not bcrypt.checkpw(
        password.encode("utf-8"),
        user.password_hash.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = str(uuid.uuid4())

    session_row = SessionToken(
        user_id=user.id,
        session_token=token
    )

    db.add(session_row)
    db.commit()

    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="lax"
    )

    return {"ok": True}


@app.get("/api/devices")
#def list_devices(db: Session = Depends(get_db)):
def list_devices(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
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

@app.post("/api/logout")
def api_logout(request: Request, response: Response, db: Session = Depends(get_db)):

    token = request.cookies.get("session_token")

    if token:
        session_row = db.scalar(
            select(SessionToken).where(SessionToken.session_token == token)
        )

        if session_row:
            db.delete(session_row)
            db.commit()

    response.delete_cookie("session_token")

    return {"ok": True}

@app.on_event("startup")
async def startup_event():
    app.state.main_loop = asyncio.get_running_loop()
    t = threading.Thread(target=_mqtt_thread, daemon=True)
    t.start()


