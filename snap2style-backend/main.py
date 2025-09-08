# main.py
from __future__ import annotations

import os, time, json, mimetypes, requests, csv, secrets, smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlencode

from fastapi import (
    FastAPI, UploadFile, File, Form, Request, Response, HTTPException,
    Depends, Cookie, Header
)
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from dotenv import load_dotenv

# Crypto / auth / db
from passlib.hash import bcrypt
from jose import jwt, JWTError
from pydantic import BaseModel, EmailStr, Field, ValidationError
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime,
    select, desc
)
from sqlalchemy.orm import declarative_base, Session, sessionmaker
from filelock import FileLock

import logging
logging.getLogger("passlib.handlers.bcrypt").setLevel(logging.ERROR)

# =========================
# ENV & CONSTANTS
# =========================
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

# AI
AI_PROVIDER        = (os.getenv("AI_PROVIDER") or "mock").lower()  # "mock" | "stability"
STABILITY_API_KEY  = os.getenv("STABILITY_API_KEY")
STABILITY_BASE     = "https://api.stability.ai"
STABILITY_ENGINE   = "stable-diffusion-xl-1024-v1-0"

# URLs
PUBLIC_BASE_URL    = os.getenv("PUBLIC_BASE_URL") or "http://127.0.0.1:8000"
FRONTEND_BASE_URL  = os.getenv("FRONTEND_BASE_URL") or "http://127.0.0.1:8000/web"

# Email (Gmail SMTP with App Password)
SMTP_HOST          = os.getenv("SMTP_HOST") or "smtp.gmail.com"                    
SMTP_PORT          = int(os.getenv("SMTP_PORT") or "587")
SMTP_USER          = os.getenv("SMTP_USER")
SMTP_PASS          = os.getenv("SMTP_PASS")
FROM_EMAIL         = os.getenv("FROM_EMAIL") or (SMTP_USER or "no-reply@example.com")
OWNER_EMAIL        = os.getenv("OWNER_EMAIL") or (SMTP_USER or FROM_EMAIL)

# Auth / JWT
JWT_SECRET         = os.getenv("JWT_SECRET") or secrets.token_urlsafe(32)
JWT_EXPIRE_MIN     = int(os.getenv("JWT_EXPIRE_MIN") or "43200")  # 30 days
AUTH_COOKIE_NAME   = "s2s_auth"

# Google OAuth
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI  = os.getenv("GOOGLE_REDIRECT_URI") or f"{PUBLIC_BASE_URL}/auth/google/callback"

# Credits / limits
GUEST_COOKIE       = "s2s_guest"
DAILY_FREE_LIMIT   = 2  # after freebies are used

# Files
UPLOAD_DIR         = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

CSV_DIR            = BASE_DIR / "analytics"
CSV_DIR.mkdir(exist_ok=True)

# DB
Base = declarative_base()
DB_PATH = BASE_DIR / "users.sqlite3"
engine = create_engine(f"sqlite:///{DB_PATH}", future=True, echo=False)
SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)

# =========================
# APP INIT & STATIC
# =========================
def abs_url(base: str, path: str) -> str:
    return f"{base.rstrip('/')}{path}"

app = FastAPI()

# Serve uploads and (optionally) /web
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
WEB_DIR = (BASE_DIR.parent / "snap2style-frontend" / "web").resolve()
if WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
js_dir = WEB_DIR / "js"
css_dir = WEB_DIR / "css"
if js_dir.exists():
    app.mount("/web/js", StaticFiles(directory=str(js_dir)), name="web-js")
if css_dir.exists():
    app.mount("/web/css", StaticFiles(directory=str(css_dir)), name="web-css")

else:
    print(f"[S2S] Frontend folder not found: {WEB_DIR}")

# Optional: redirect "/" to login page
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/web/login.html", status_code=302)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500", "http://localhost:5500",
        "http://127.0.0.1:5501", "http://localhost:5501",
        "http://127.0.0.1:5000", "http://localhost:5000",
        "http://127.0.0.1:5001", "http://localhost:5001",
        "http://127.0.0.1:3000", "http://localhost:3000",
        "http://127.0.0.1:8000", "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# No-cache for served images/downloads
class NoCache(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        p = request.url.path
        if p.startswith("/uploads") or p.startswith("/download"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

app.add_middleware(NoCache)

# =========================
# MODELS
# =========================
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(320), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    free_credits = Column(Integer, default=0, nullable=False)  # +2 after verify
    verify_bonus_claimed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)

class EmailToken(Base):
    __tablename__ = "email_tokens"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    token = Column(String(128), unique=True, index=True, nullable=False)  # link token OR 6-digit OTP
    purpose = Column(String(32), nullable=False)  # "verify" | "reset" | "otp"
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Guest(Base):
    __tablename__ = "guests"
    id = Column(String(64), primary_key=True)  # cookie UUID
    credits = Column(Integer, default=2, nullable=False)  # 2 pre-reg freebies
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)

class GenerationLog(Base):
    __tablename__ = "generation_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=True)
    guest_id = Column(String(64), index=True, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

Base.metadata.create_all(engine)

# =========================
# EMAIL + CSV LOGGING
# =========================
def send_email(to: str, subject: str, html: str):
    # Dev fallback
    if not (SMTP_USER and SMTP_PASS):
        print("\n=== EMAIL (DEV MODE) ===")
        print("TO:", to)
        print("SUBJECT:", subject)
        print(html)
        print("=======================\n")
        return
    msg = MIMEText(html, "html")
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = to
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)

def _append_csv(filename: str, row: dict, headers: list[str]):
    path = CSV_DIR / filename
    lock = FileLock(str(path) + ".lock")
    with lock:
        newfile = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            if newfile:
                w.writeheader()
            w.writerow(row)

def log_registration_csv(user_id: int, email: str, request: Request, verified: bool = False):
    _append_csv(
        "registrations.csv",
        {
            "ts": datetime.utcnow().isoformat(),
            "user_id": user_id,
            "email": email,
            "verified": int(verified),
            "ip": request.client.host if request.client else "",
            "ua": request.headers.get("user-agent", ""),
        },
        ["ts","user_id","email","verified","ip","ua"]
    )

def log_purchase_csv(user_id: Optional[int], email: Optional[str], amount_rs: int,
                     order_id: str, payment_id: str, status: str, request: Request):
    _append_csv(
        "purchases.csv",
        {
            "ts": datetime.utcnow().isoformat(),
            "user_id": user_id or "",
            "email": email or "",
            "amount_rs": amount_rs,
            "order_id": order_id,
            "payment_id": payment_id,
            "status": status,
            "ip": request.client.host if request.client else "",
            "ua": request.headers.get("user-agent", ""),
        },
        ["ts","user_id","email","amount_rs","order_id","payment_id","status","ip","ua"]
    )

def log_generation_csv(kind: str, identifier: str, result_url: str, provider: str,
                       status: str, style: str, instructions_len: int, request: Request):
    _append_csv(
        "generations.csv",
        {
            "ts": datetime.utcnow().isoformat(),
            "kind": kind,
            "id": identifier,
            "provider": provider,
            "status": status,  # success|fallback
            "style": style or "",
            "instructions_len": instructions_len,
            "result_url": result_url,
            "ip": request.client.host if request.client else "",
            "ua": request.headers.get("user-agent", ""),
        },
        ["ts","kind","id","provider","status","style","instructions_len","result_url","ip","ua"]
    )

# =========================
# AUTH HELPERS
# =========================
def hash_pw(p: str) -> str:
    return bcrypt.hash(p)

def verify_pw(p: str, h: str) -> bool:
    try:
        return bcrypt.verify(p, h)
    except Exception:
        return False

def make_jwt(user_id: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MIN)
    return jwt.encode({"sub": str(user_id), "exp": exp}, JWT_SECRET, algorithm="HS256")

def parse_jwt(token: str) -> Optional[int]:
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return int(data.get("sub"))
    except JWTError:
        return None

def new_email_token(user_id: int, purpose: str, minutes: int = 60*24*3) -> str:
    tok = secrets.token_urlsafe(32)
    with SessionLocal() as db:
        db.add(EmailToken(
            user_id=user_id,
            token=tok,
            purpose=purpose,
            expires_at=datetime.utcnow() + timedelta(minutes=minutes)
        ))
        db.commit()
    return tok

def get_current_user(token: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME)) -> Optional[User]:
    if not token:
        return None
    uid = parse_jwt(token)
    if not uid:
        return None
    with SessionLocal() as db:
        return db.get(User, uid)

# OTP helpers
def generate_otp_for_user(user_id: int, minutes: int = 15) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = datetime.utcnow()
    with SessionLocal() as db:
        old = db.execute(
            select(EmailToken).where(EmailToken.user_id == user_id, EmailToken.purpose == "otp")
        ).scalars().all()
        for t in old:
            db.delete(t)
        db.add(EmailToken(
            user_id=user_id,
            token=code,
            purpose="otp",
            expires_at=now + timedelta(minutes=minutes),
        ))
        db.commit()
    return code

def send_otp_email(to_email: str, code: str):
    html = f"""
      <h3>Your Snap2Style verification code</h3>
      <p style="font-size:18px;letter-spacing:3px;margin:12px 0"><b>{code}</b></p>
      <p>This code expires in 15 minutes.</p>
    """
    send_email(to_email, "Your Snap2Style verification code", html)

# =========================
# GUEST & LIMIT HELPERS
# =========================
from uuid import uuid4

def get_or_create_guest(request: Request, response: Response) -> Guest:
    gid = request.cookies.get(GUEST_COOKIE)
    if not gid:
        gid = uuid4().hex
        response.set_cookie(GUEST_COOKIE, gid, httponly=True, samesite="Lax", max_age=60*60*24*30)
    with SessionLocal() as db:
        g = db.get(Guest, gid)
        if not g:
            g = Guest(id=gid, credits=2)
            db.add(g); db.commit(); db.refresh(g)
        else:
            g.last_seen = datetime.utcnow()
            db.commit(); db.refresh(g)
        return g

def log_generation_db(user_id: int | None = None, guest_id: str | None = None):
    with SessionLocal() as db:
        db.add(GenerationLog(user_id=user_id, guest_id=guest_id))
        db.commit()

def count_last_24h(user_id: int | None = None, guest_id: str | None = None) -> int:
    since = datetime.utcnow() - timedelta(hours=24)
    with SessionLocal() as db:
        stmt = select(GenerationLog).where(GenerationLog.created_at >= since)
        if user_id:
            stmt = stmt.where(GenerationLog.user_id == user_id)
        if guest_id:
            stmt = stmt.where(GenerationLog.guest_id == guest_id)
        return len(db.execute(stmt).scalars().all())

def next_available_ts(user_id: int) -> Optional[float]:
    since = datetime.utcnow() - timedelta(hours=24)
    with SessionLocal() as db:
        stmt = (
            select(GenerationLog)
            .where(GenerationLog.user_id == user_id, GenerationLog.created_at >= since)
            .order_by(desc(GenerationLog.created_at))
        )
        logs = db.execute(stmt).scalars().all()
        if len(logs) < DAILY_FREE_LIMIT:
            return None
        kth = logs[DAILY_FREE_LIMIT - 1]
        return (kth.created_at + timedelta(hours=24)).timestamp()

# =========================
# PROMPT PLANNING
# =========================
def plan_from_instructions(text: str) -> dict:
    text = (text or "").strip() or "refined, tasteful style with clean lines and natural materials"
    positive = "keep room layout and camera angle. photorealistic lighting and shadows. " + text
    negative = ("low quality, blurry, text, watermark, people, extra windows, duplicated walls, "
                "distorted furniture, warped geometry")
    return {"positive": positive, "negative": negative, "image_strength": 0.55, "steps": 28, "cfg_scale": 7.0}

def build_prompt_from_style(style: str) -> Tuple[str, str, float, int, float]:
    STYLE_PROMPTS = {
        "minimal":   "clean lines, neutral palette, scandinavian furniture, lots of natural light",
        "cozy":      "warm lighting, soft textures, layered textiles, plants, inviting atmosphere",
        "industrial":"exposed brick, concrete, metal accents, matte black fixtures",
        "luxury":    "marble surfaces, brass details, velvet upholstery, statement lighting",
    }
    base = STYLE_PROMPTS.get(style or "minimal", STYLE_PROMPTS["minimal"])
    pos = f"keep room layout and camera angle. photorealistic lighting and shadows. {base}"
    neg = "low quality, blurry, text, watermark, people, extra windows, duplicated walls, distorted furniture"
    return pos, neg, 0.6, 28, 7.0

# =========================
# STABILITY CALL
# =========================
def stability_img2img(init_image_path: str, prompt: str, negative_prompt: str,
                      image_strength: float = 0.55, steps: int = 28, cfg_scale: float = 7.0) -> bytes:
    if not STABILITY_API_KEY:
        raise RuntimeError("Set STABILITY_API_KEY in .env")
    url = f"{STABILITY_BASE}/v1/generation/{STABILITY_ENGINE}/image-to-image"
    headers = {"Authorization": f"Bearer {STABILITY_API_KEY}"}
    mime = mimetypes.guess_type(init_image_path)[0] or "image/png"
    with open(init_image_path, "rb") as f:
        files = {"init_image": (os.path.basename(init_image_path), f, mime)}
        data = {
            "image_strength": str(image_strength),
            "steps": str(steps),
            "cfg_scale": str(cfg_scale),
            "samples": "1",
            "output_format": "png",
            "text_prompts[0][text]": prompt,
            "text_prompts[0][weight]": "1",
            "text_prompts[1][text]": negative_prompt,
            "text_prompts[1][weight]": "-1",
        }
        r = requests.post(url, headers=headers, files=files, data=data, timeout=120)
    ct = r.headers.get("content-type", "")
    if "application/json" in ct:
        if r.status_code >= 400:
            raise requests.HTTPError(f"{r.status_code} {r.text}")
        payload = r.json()
        arts = payload.get("artifacts") or []
        if not arts or not arts[0].get("base64"):
            raise RuntimeError(f"Stability returned no image: {payload}")
        import base64
        return base64.b64decode(arts[0]["base64"])
    r.raise_for_status()
    return r.content

# =========================
# SCHEMAS
# =========================
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class OtpRequestIn(BaseModel):
    email: EmailStr

class OtpVerifyIn(BaseModel):
    email: EmailStr
    code: str

# =========================
# DIAGNOSTICS / PAGES
# =========================
@app.get("/env-check")
def env_check():
    return {
        "provider": AI_PROVIDER,
        "stability_key": bool(STABILITY_API_KEY),
        "public_base_url": PUBLIC_BASE_URL,
        "google_oauth": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
    }

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)

@app.get("/test", response_class=HTMLResponse)
def test_page(request: Request):
    html = r"""<!doctype html><meta charset="utf-8"><title>S2S Test</title>
<body style="font-family:system-ui;padding:24px;max-width:720px">
<h2>Snap2Style Test</h2>
<div id="status"></div>
<div id="dropzone" style="padding:20px;border:2px dashed #bbb;cursor:pointer;max-width:480px">
 Click or drop an image here
 <input id="fileInput" type="file" accept="image/*" style="position:absolute;left:-9999px;width:1px;height:1px;opacity:0">
</div>
<p><img id="preview" style="max-width:480px;display:none"/></p>
<textarea id="instructions" placeholder="e.g. light grey walls" style="width:480px;height:64px"></textarea><br>
<button id="submitBtn">Style</button> <button id="resetBtn">Reset</button>
<div id="result" style="margin-top:16px">
  <img id="imgAfter" style="max-width:480px;display:block"/>
  <p><a id="openFull" href="#" target="_blank" rel="noopener">Open full</a> | <a id="downloadBtn" class="hidden">Download</a></p>
</div>
<script>
const API="__API__";
const S=document.getElementById("status"), dz=document.getElementById("dropzone"), fi=document.getElementById("fileInput"),
      prev=document.getElementById("preview"), sub=document.getElementById("submitBtn"), rst=document.getElementById("resetBtn"),
      after=document.getElementById("imgAfter"), openFull=document.getElementById("openFull"), dl=document.getElementById("downloadBtn"),
      instr=document.getElementById("instructions");
let currentFile=null;
dz.addEventListener("click",()=>{ fi.value=""; fi.click(); });
dz.addEventListener("dragover",e=>{ e.preventDefault(); });
dz.addEventListener("drop",e=>{ e.preventDefault(); const f=e.dataTransfer.files?.[0]; if(f){ currentFile=f; const r=new FileReader(); r.onload=ev=>{ prev.src=ev.target.result; prev.style.display="block"; }; r.readAsDataURL(f); }});
fi.addEventListener("change",()=>{ const f=fi.files?.[0]; if(!f) return; currentFile=f; const r=new FileReader(); r.onload=ev=>{ prev.src=ev.target.result; prev.style.display="block"; }; r.readAsDataURL(f); });
sub.addEventListener("click", async ()=>{
  const f=currentFile||fi.files?.[0]; if(!f){ S.textContent="Pick an image first"; return; }
  S.textContent="Styling…";
  const fd=new FormData(); fd.append("style",""); fd.append("instructions", instr.value||""); fd.append("file", f);
  const res=await fetch(API+"/style-image",{method:"POST",body:fd,credentials:"include",cache:"no-store"}); const txt=await res.text(); let data; try{data=JSON.parse(txt)}catch{}
  if(!res.ok){ S.textContent=(data&&(data.error||data.detail))||("HTTP "+res.status); return; }
  const url=data.styledUrls&&data.styledUrls[0]; if(!url){ S.textContent="No image URL"; return; }
  const final=/^https?:\/\//i.test(url)?url:API+url;
  after.src=final+(final.includes("?")?"&":"?")+"t="+Date.now(); openFull.href=final;
  const fname=final.split("/").pop().split("?")[0]; dl.href=API+"/download/"+fname; dl.download=fname; dl.classList.remove("hidden");
  S.textContent="Done.";
});
rst.addEventListener("click",()=>{ currentFile=null; fi.value=""; prev.removeAttribute("src"); prev.style.display="none"; after.removeAttribute("src"); S.textContent=""; dl.classList.add("hidden"); });
</script>
</body>"""
    base = str(request.base_url).rstrip("/")
    return HTMLResponse(html.replace("__API__", base))

# =========================
# AUTH ROUTES (link + OTP)
# =========================
@app.post("/auth/register")
async def register(request: Request, response: Response):
    # Accept JSON or form-data (avoid 422 UX)
    payload = {}
    ct = (request.headers.get("content-type") or "").lower()
    try:
        if "application/json" in ct:
            payload = await request.json()
        else:
            form = await request.form()
            payload = dict(form)
    except Exception:
        pass

    try:
        data = RegisterIn.model_validate({
            "email": (payload.get("email") or "").strip().lower(),
            "password": payload.get("password") or "",
        })
    except ValidationError as ve:
        return JSONResponse(status_code=400, content={"error": "Invalid request body", "details": ve.errors()})

    email = data.email
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == email)):
            return JSONResponse(status_code=409, content={"error":"Email already registered"})
        user = User(email=email, password_hash=hash_pw(data.password), is_verified=False, free_credits=0)
        db.add(user); db.commit(); db.refresh(user)

        tok = new_email_token(user.id, "verify", minutes=60*24*3)
        link = f"{PUBLIC_BASE_URL}/auth/verify?token={tok}"
        send_email(user.email, "Verify your Snap2Style email",
                   f"<h3>Verify your email</h3><p>Click to verify: <a href='{link}'>{link}</a></p>")

        try:
            code = generate_otp_for_user(user.id, minutes=15)
            send_otp_email(user.email, code)
        except Exception:
            pass

        try:
            send_email(SMTP_USER or FROM_EMAIL, "New registration", f"<p>Email: {user.email}</p><p>User ID: {user.id}</p>")
            log_registration_csv(user.id, user.email, request, verified=False)
        except Exception:
            pass

        response.set_cookie(AUTH_COOKIE_NAME, make_jwt(user.id), httponly=True, samesite="Lax", max_age=60*60*24*30)
        return {"ok": True, "message":"Registered. Check your inbox for the verification link or OTP code."}

@app.get("/auth/verify")
def verify_email(token: str, request: Request, response: Response):
    now = datetime.utcnow()
    with SessionLocal() as db:
        tok = db.scalar(select(EmailToken).where(EmailToken.token==token, EmailToken.purpose=="verify"))
        if not tok or tok.expires_at < now:
            return JSONResponse(status_code=400, content={"error":"Invalid or expired token"})
        u = db.get(User, tok.user_id)
        if not u:
            return JSONResponse(status_code=400, content={"error":"User not found"})
        u.is_verified = True
        if not u.verify_bonus_claimed:
            u.free_credits = (u.free_credits or 0) + 2
            u.verify_bonus_claimed = True
        db.delete(tok); db.commit()
        try:
            send_email(SMTP_USER or FROM_EMAIL, "User verified", f"<p>Email: {u.email}</p><p>User ID: {u.id}</p>")
            log_registration_csv(u.id, u.email, request, verified=True)
        except Exception:
            pass
    response.set_cookie(AUTH_COOKIE_NAME, make_jwt(u.id), httponly=True, samesite="Lax", max_age=60*60*24*30)
    return RedirectResponse(url=f"{FRONTEND_BASE_URL}/login.html?verified=1", status_code=302)

@app.post("/auth/request-otp")
def request_otp(data: OtpRequestIn):
    email = data.email.lower().strip()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            return JSONResponse(status_code=404, content={"error":"User not found"})
        if user.is_verified:
            return {"ok": True, "message": "Already verified"}
        code = generate_otp_for_user(user.id, minutes=15)
        try:
            send_otp_email(user.email, code)
        except Exception:
            pass
    return {"ok": True, "message": "OTP sent"}

@app.post("/auth/resend-otp")
def resend_otp(data: OtpRequestIn):
    return request_otp(data)

@app.post("/auth/verify-otp")
def verify_otp(data: OtpVerifyIn, response: Response, request: Request):
    email = data.email.lower().strip()
    code  = (data.code or "").strip()
    if not (len(code) == 6 and code.isdigit()):
        return JSONResponse(status_code=400, content={"error":"Invalid code format"})
    now = datetime.utcnow()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            return JSONResponse(status_code=404, content={"error":"User not found"})
        tok = db.scalar(
            select(EmailToken).where(
                EmailToken.user_id == user.id,
                EmailToken.purpose == "otp",
                EmailToken.expires_at >= now,
            )
        )
        if not tok or tok.token != code:
            return JSONResponse(status_code=400, content={"error":"Invalid or expired code"})
        user.is_verified = True
        if not user.verify_bonus_claimed:
            user.free_credits = (user.free_credits or 0) + 2
            user.verify_bonus_claimed = True
        db.delete(tok); db.commit()
        try:
            send_email(SMTP_USER or FROM_EMAIL, "User verified (OTP)", f"<p>Email: {user.email}</p><p>User ID: {user.id}</p>")
            log_registration_csv(user.id, user.email, request, verified=True)
        except Exception:
            pass
    response.set_cookie(AUTH_COOKIE_NAME, make_jwt(user.id), httponly=True, samesite="Lax", max_age=60*60*24*30)
    return {"ok": True}

# ---- Login (accepts JSON or Form; no more 422) ----
@app.post("/auth/login")
async def login(request: Request, response: Response):
    payload = {}
    ct = (request.headers.get("content-type") or "").lower()
    try:
        if "application/json" in ct:
            payload = await request.json()
        else:
            form = await request.form()
            payload = dict(form)
    except Exception:
        pass

    try:
        data = LoginIn.model_validate({
            "email": (payload.get("email") or "").strip().lower(),
            "password": payload.get("password") or "",
        })
    except ValidationError as ve:
        return JSONResponse(status_code=400, content={"error": "Invalid request body", "details": ve.errors()})

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email==data.email))
        if not user or not verify_pw(data.password, user.password_hash):
            return JSONResponse(status_code=401, content={"error":"Invalid email or password"})
        user.last_login_at = datetime.utcnow()
        db.commit()

    response.set_cookie(AUTH_COOKIE_NAME, make_jwt(user.id), httponly=True, samesite="Lax", max_age=60*60*24*30)
    return {"ok": True}

@app.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(AUTH_COOKIE_NAME)
    return {"ok": True}

@app.post("/auth/resend")
def resend_verify(user: Optional[User] = Depends(get_current_user)):
    if not user:
        return JSONResponse(status_code=401, content={"error":"Not logged in"})
    if user.is_verified:
        return {"ok": True, "message":"Already verified"}
    tok = new_email_token(user.id, "verify", minutes=60*24*3)
    link = f"{PUBLIC_BASE_URL}/auth/verify?token={tok}"
    send_email(user.email, "Verify your Snap2Style email", f"<p>Click: <a href='{link}'>{link}</a></p>")
    return {"ok": True}

# =========================
# GOOGLE OAUTH (redirect flow)
# =========================
@app.get("/auth/google/start")
def google_start(response: Response):
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        return JSONResponse(status_code=500, content={"error":"Google OAuth not configured"})
    state = secrets.token_urlsafe(16)
    response.set_cookie("s2s_oauth_state", state, httponly=True, samesite="Lax", max_age=600)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return RedirectResponse(url)

@app.get("/auth/google/callback")
def google_callback(
    request: Request,
    response: Response,
    code: Optional[str] = None,
    state: Optional[str] = None,
    s2s_oauth_state: Optional[str] = Cookie(default=None),
):
    if not code:
        return JSONResponse(status_code=400, content={"error":"Missing code"})
    if not state or not s2s_oauth_state or state != s2s_oauth_state:
        return JSONResponse(status_code=400, content={"error":"Invalid state"})

    data = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    tok = requests.post("https://oauth2.googleapis.com/token", data=data, timeout=20)
    if tok.status_code != 200:
        return JSONResponse(status_code=400, content={"error":"Token exchange failed", "details": tok.text})
    t = tok.json()
    access_token = t.get("access_token")
    if not access_token:
        return JSONResponse(status_code=400, content={"error":"No access token"})

    ui = requests.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20
    )
    if ui.status_code != 200:
        return JSONResponse(status_code=400, content={"error":"Failed to fetch userinfo", "details": ui.text})
    uinfo = ui.json()
    email = (uinfo.get("email") or "").lower().strip()
    email_verified = bool(uinfo.get("email_verified"))
    if not email:
        return JSONResponse(status_code=400, content={"error":"Google did not return an email"})

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            rnd = secrets.token_urlsafe(16)
            user = User(
                email=email,
                password_hash=hash_pw(rnd),
                is_verified=email_verified,
                free_credits=2 if email_verified else 0,
                verify_bonus_claimed=email_verified,
            )
            db.add(user); db.commit(); db.refresh(user)
        else:
            changed = False
            if email_verified and not user.is_verified:
                user.is_verified = True; changed = True
            if email_verified and not user.verify_bonus_claimed:
                user.free_credits = (user.free_credits or 0) + 2
                user.verify_bonus_claimed = True; changed = True
            if changed: db.commit()

    response.delete_cookie("s2s_oauth_state")
    response.set_cookie(AUTH_COOKIE_NAME, make_jwt(user.id), httponly=True, samesite="Lax", max_age=60*60*24*30)
    return RedirectResponse(url=f"{FRONTEND_BASE_URL}/snap.html?google=1")

# ---- GIS: Google Identity Services ID token endpoint (for the button) ----
@app.post("/auth/google-idtoken")
async def google_idtoken_login(request: Request, response: Response):
    try:
        body = await request.json()
    except Exception:
        body = {}
    token = body.get("id_token") or body.get("credential")
    if not token:
        raise HTTPException(status_code=400, detail="Missing id_token")

    try:
        from google.oauth2 import id_token as gidt
        from google.auth.transport import requests as grequests
        info = gidt.verify_oauth2_token(token, grequests.Request())
        if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
            raise ValueError("Invalid issuer")
        email = info.get("email")
        if not email:
            raise ValueError("No email in token")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid Google token: {e}")

    with SessionLocal() as db:
        u = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if not u:
            u = User(email=email, password_hash=hash_pw(secrets.token_hex(12)), is_verified=True, free_credits=2)
            db.add(u); db.commit(); db.refresh(u)
        token = make_jwt(u.id)
        response.set_cookie(AUTH_COOKIE_NAME, token, httponly=True, samesite="Lax", max_age=60*60*24*30)
        response.delete_cookie(GUEST_COOKIE, path="/")
        return {"ok": True, "email": email, "kind": "user"}

# =========================
# CREDITS API
# =========================
@app.get("/credits")
def credits(request: Request, response: Response, user: Optional[User] = Depends(get_current_user)):
    if user:
        with SessionLocal() as db:
            u = db.get(User, user.id)
            used24 = count_last_24h(user_id=u.id)
            na = next_available_ts(u.id)
            return {
                "kind": "user",
                "email": u.email,
                "verified": u.is_verified,
                "free_credits": u.free_credits,
                "daily_limit": DAILY_FREE_LIMIT,
                "used_last_24h": used24,
                "next_available_ts": na,
            }
    g = get_or_create_guest(request, response)
    return {
        "kind": "guest",
        "verified": False,
        "guest_credits": g.credits,
        "daily_limit": 0,
        "used_last_24h": count_last_24h(guest_id=g.id),
        "next_available_ts": None,
    }

# =========================
# STYLE IMAGE (credits + daily limit)
# =========================
@app.post("/style-image")
async def style_image(
    request: Request,
    response: Response,
    style: str = Form(""),
    instructions: str = Form(""),
    file: UploadFile = File(...),
    user: Optional[User] = Depends(get_current_user),
):
    ctype = (file.content_type or "").lower()
    if not ctype.startswith("image/"):
        return JSONResponse(status_code=400, content={"error": "Invalid file type"})
    contents = await file.read()
    if len(contents) > 8 * 1024 * 1024:
        return JSONResponse(status_code=400, content={"error": "File too large"})

    safe_name = f"{int(time.time())}_{file.filename.replace(' ', '_')}"
    in_path = UPLOAD_DIR / safe_name
    in_path.write_bytes(contents)

    # Always use the actual request origin to build URLs so images load
    base = str(request.base_url).rstrip("/")

    # ---- ENFORCE CREDITS ----
    who_kind, who_id = "guest", ""
    if user:
        with SessionLocal() as db:
            u = db.get(User, user.id)
            if u.is_verified and u.free_credits > 0:
                u.free_credits -= 1; db.commit()
                who_kind, who_id = "user", str(u.id)
            else:
                used24 = count_last_24h(user_id=u.id)
                if used24 >= DAILY_FREE_LIMIT:
                    ts = next_available_ts(u.id)
                    wait = int(max(0, (ts - datetime.utcnow().timestamp()))) if ts else 3600
                    return JSONResponse(
                        status_code=429,
                        content={
                            "error": "Daily free limit reached.",
                            "used_last_24h": used24,
                            "daily_limit": DAILY_FREE_LIMIT,
                            "retry_after_seconds": wait,
                            "message": "Please wait or purchase a plan to continue.",
                        }
                    )
                who_kind, who_id = "user", str(u.id)
    else:
        g = get_or_create_guest(request, response)
        with SessionLocal() as db:
            gg = db.get(Guest, g.id)
            if gg.credits <= 0:
                return JSONResponse(
                    status_code=402,
                    content={"error": "Free tries used. Register & verify to get +2, then 2/day.", "cta": "register"}
                )
            gg.credits -= 1; db.commit()
        who_kind, who_id = "guest", g.id
    # ---- END ENFORCEMENT ----

    # ---- GENERATION ----
    payload = None
    if AI_PROVIDER == "mock":
        payload = {
            "styledUrls": [abs_url(base, f"/uploads/{safe_name}")],
            "filename": safe_name,
            "predictionId": "mock",
            "style": style or "custom",
            "note": "Mock mode: set AI_PROVIDER=stability to enable real styling."
        }
    elif AI_PROVIDER == "stability":
        try:
            if (instructions or "").strip():
                plan = plan_from_instructions(instructions)
                pos, neg = plan["positive"], plan["negative"]
                image_strength = float(plan.get("image_strength", 0.55))
                steps = int(plan.get("steps", 28))
                cfg = float(plan.get("cfg_scale", 7.0))
            else:
                pos, neg, image_strength, steps, cfg = build_prompt_from_style(style)

            png = stability_img2img(str(in_path), pos, neg, image_strength, steps, cfg)
            out_name = f"{int(time.time())}_styled.png"
            (UPLOAD_DIR / out_name).write_bytes(png)
            payload = {
                "styledUrls": [abs_url(base, f"/uploads/{out_name}")],
                "filename": out_name,
                "predictionId": "stability",
                "style": style or "custom"
            }
        except Exception as e:
            payload = {
                "styledUrls": [abs_url(base, f"/uploads/{safe_name}")],
                "filename": safe_name,
                "predictionId": "error_fallback",
                "style": style or "custom",
                "note": f"Generation error: {str(e)}",
            }
    else:
        return JSONResponse(status_code=400, content={"error": "Unknown AI provider"})

    # log after generation (DB + CSV)
    try:
        if who_kind == "user":
            log_generation_db(user_id=int(who_id))
        else:
            log_generation_db(guest_id=who_id)
        log_generation_csv(
            kind=who_kind, identifier=who_id,
            result_url=payload["styledUrls"][0],
            provider=AI_PROVIDER,
            status=("success" if payload.get("predictionId") in ("mock","stability") else "fallback"),
            style=style, instructions_len=len(instructions or ""), request=request
        )
    except Exception:
        pass

    return payload

# =========================
# DOWNLOAD
# =========================
@app.get("/download/{name}")
def download_image(name: str):
    p = (UPLOAD_DIR / name).resolve()
    if not p.exists() or UPLOAD_DIR.resolve() not in p.parents:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(p, media_type="application/octet-stream", filename=name)

# =========================
# RAZORPAY WEBHOOK (stub)
# =========================
@app.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request, x_razorpay_signature: Optional[str] = Header(default=None)):
    payload = await request.body()
    data = {}
    try:
        data = json.loads(payload.decode("utf-8"))
    except Exception:
        pass
    entity = (((data.get("payload") or {}).get("payment") or {}).get("entity") or {})
    status = entity.get("status") or "created"
    amount_rs = int(entity.get("amount", 0)) // 100
    order_id = entity.get("order_id") or ""
    payment_id = entity.get("id") or ""
    email = entity.get("email") or ""
    # TODO: verify signature
    try:
        log_purchase_csv(None, email, amount_rs, order_id, payment_id, status, request)
        send_email(SMTP_USER or FROM_EMAIL, "Payment event",
                   f"<p>Status: {status}</p><p>₹{amount_rs}</p><p>Order: {order_id}</p><p>Payment: {payment_id}</p><p>Email: {email}</p>")
    except Exception:
        pass
    return {"ok": True}

# =========================
# DEBUG
# =========================
@app.get("/debug/send-test-email")
def send_test_email(to: EmailStr):
    send_email(to, "S2S test", "<p>Hello from Snap2Style backend ✅</p>")
    return {"ok": True}

js_dir  = WEB_DIR / "js"
css_dir = WEB_DIR / "css"
img_dir = WEB_DIR / "img"  

if js_dir.exists():
    app.mount("/js",  StaticFiles(directory=str(js_dir)),  name="js")
if css_dir.exists():
    app.mount("/css", StaticFiles(directory=str(css_dir)), name="css")
if img_dir.exists():
    app.mount("/img", StaticFiles(directory=str(img_dir)), name="img")
