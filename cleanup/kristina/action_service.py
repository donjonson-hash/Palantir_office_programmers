import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any
import hashlib
import json
import time
import asyncio
import websockets
import httpx

app = FastAPI(title="Kristina Action Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENCLAW_URL = "ws://localhost:18789"
OPENCLAW_API_KEY = os.environ["OPENCLAW_API_KEY"]  # из окружения; нет значения в коде

class ActionRequest(BaseModel):
    action: str
    target: str
    agent_id: str
    params: Optional[Dict[str, Any]] = {}
    requires_approval: bool = False

class ActionResponse(BaseModel):
    status: str
    action_id: str
    message: str

audit_log = []

def log_action(action_req: ActionRequest, status: str, result: str = ""):
    entry = {
        "action_id": hashlib.md5(f"{time.time()}{action_req.action}".encode()).hexdigest()[:8],
        "agent_id": action_req.agent_id,
        "action": action_req.action,
        "target": action_req.target,
        "params": action_req.params,
        "timestamp": time.time(),
        "status": status,
        "result": result,
    }
    audit_log.append(entry)
    print(f"[AUDIT] {entry}")
    return entry["action_id"]

async def execute_with_openclaw(action: str, target: str, params: dict) -> str:
    """Выполняет действие через HTTP API OpenClaw"""
    
    async with httpx.AsyncClient() as client:
        try:
            # Пробуем разные эндпоинты
            endpoints = [
                ("POST", "/api/execute", {"action": action, "target": target, **params}),
                ("POST", "/command", {"cmd": action, "args": params}),
                ("GET", f"/api/action/{action}", {"target": target}),
            ]
            
            for method, endpoint, data in endpoints:
                url = f"http://localhost:18789{endpoint}"
                if method == "POST":
                    resp = await client.post(url, json=data, timeout=5)
                else:
                    resp = await client.get(url, params=data, timeout=5)
                
                if resp.status_code == 200:
                    return f"✅ OpenClaw HTTP: {resp.json()}"
            
            return "⚠️ No working HTTP endpoint found"
            
        except Exception as e:
            return f"⚠️ OpenClaw HTTP error: {str(e)}"

async def execute_with_openclaw(action: str, target: str, params: dict) -> str:
    try:
        async with websockets.connect(OPENCLAW_URL) as ws:
            # Получаем challenge
            challenge_msg = await asyncio.wait_for(ws.recv(), timeout=10)
            challenge = json.loads(challenge_msg)
            
            if challenge.get("type") == "event" and challenge.get("event") == "connect.challenge":
                nonce = challenge["payload"]["nonce"]
                auth_response = json.dumps({
                    "type": "auth.response",
                    "payload": {"api_key": OPENCLAW_API_KEY, "nonce": nonce}
                })
                await ws.send(auth_response)
            
            # Отправляем команду
            cmd = json.dumps({"url": params.get("url", "https://google.com")})
            await ws.send(cmd)
            response = await asyncio.wait_for(ws.recv(), timeout=30)
            return f"✅ OpenClaw executed: {response}"
    except Exception as e:
        return f"⚠️ OpenClaw error: {str(e)}"

async def execute_action(action: str, target: str, params: dict) -> str:
    await asyncio.sleep(0.2)
    return await execute_with_openclaw(action, target, params)

@app.post("/api/actions/execute", response_model=ActionResponse)
async def execute(request: ActionRequest):
    print(f"[REQUEST] {request.agent_id} → {request.action} on {request.target}")
    
    if request.requires_approval:
        action_id = log_action(request, "pending_approval")
        return ActionResponse(status="pending_approval", action_id=action_id, message="Awaiting approval")
    
    try:
        result = await execute_action(request.action, request.target, request.params)
        action_id = log_action(request, "executed", result)
        return ActionResponse(status="executed", action_id=action_id, message=result)
    except Exception as e:
        action_id = log_action(request, "failed", str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/actions/audit")
async def get_audit_log(limit: int = 50):
    return {"logs": audit_log[-limit:]}

@app.get("/api/actions/health")
async def health_check():
    return {"status": "ok", "audit_count": len(audit_log)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
