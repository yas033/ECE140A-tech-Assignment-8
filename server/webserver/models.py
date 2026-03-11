from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import relationship
from db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    sessions = relationship("SessionToken", back_populates="user")


class SessionToken(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    session_token = Column(String(255), unique=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User", back_populates="sessions")


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True)
    mac_address = Column(String(32), unique=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    readings = relationship("Reading", back_populates="device")


class Reading(Base):
    __tablename__ = "readings"

    id = Column(Integer, primary_key=True)
    mac_address = Column(String(32))
    device_id = Column(Integer, ForeignKey("devices.id"))

    thermistor_temp = Column(Float)
    prediction = Column(String(16))
    confidence = Column(Float)

    pixels = Column(JSON)
    created_at = Column(DateTime, server_default=func.now())

    device = relationship("Device", back_populates="readings")