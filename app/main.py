import os
import random
import string

import redis
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import Column, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session

app = FastAPI(title="URL Shortener")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@db:5432/urlshortener")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
BASE_URL = os.getenv("BASE_URL", "http://localhost")

engine = create_engine(DATABASE_URL)
redis_client = redis.from_url(REDIS_URL, decode_responses=True)


class Base(DeclarativeBase):
    pass


class URLRecord(Base):
    __tablename__ = "urls"
    short_code = Column(String, primary_key=True, index=True)
    original_url = Column(String, nullable=False)


Base.metadata.create_all(bind=engine)


class ShortenRequest(BaseModel):
    url: str


class ShortenResponse(BaseModel):
    short_url: str
    short_code: str


def generate_code(length: int = 6) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=length))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/shorten", response_model=ShortenResponse)
def shorten_url(request: ShortenRequest):
    with Session(engine) as session:
        code = generate_code()
        # Ensure uniqueness
        while session.get(URLRecord, code):
            code = generate_code()

        record = URLRecord(short_code=code, original_url=request.url)
        session.add(record)
        session.commit()

    # Cache in Redis for fast redirects (TTL 1 hour)
    redis_client.setex(code, 3600, request.url)

    return ShortenResponse(short_url=f"{BASE_URL}/{code}", short_code=code)


@app.get("/{short_code}")
def redirect_url(short_code: str):
    # Check Redis cache first
    cached = redis_client.get(short_code)
    if cached:
        return RedirectResponse(url=cached, status_code=302)

    # Fallback to database
    with Session(engine) as session:
        record = session.get(URLRecord, short_code)
        if not record:
            raise HTTPException(status_code=404, detail="Short URL not found")
        # Repopulate cache
        redis_client.setex(short_code, 3600, record.original_url)
        return RedirectResponse(url=record.original_url, status_code=302)
