"""Proxy local « compatible OpenAI » vers `claude -p` (Claude Code, avec
l'abonnement de l'utilisateur). Aucun outil, aucun contexte de dépôt
(répertoire de travail vide, pas de réglages ni de MCP).

    python3 -I proxy.py [port]      # défaut 18030 (ou PROXY_PORT)

Variables : PROXY_MODEL (défaut claude-opus-5-5), PROXY_HOST (127.0.0.1),
PROXY_PORT (18030), PROXY_LOG (fichier de journal ; sinon stderr),
PROXY_TIMEOUT (secondes par appel, 1800).

GET  /v1/models            -> le modèle servi
POST /v1/chat/completions  -> messages (system + conversation), response_format
                              json_schema (sortie structurée), stream (SSE en
                              un seul morceau). Les `tools` sont refusés.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = os.environ.get("PROXY_MODEL", "claude-opus-5-5")
WORKDIR = tempfile.mkdtemp(prefix="claude-proxy-")
LOG = os.environ.get("PROXY_LOG")
TIMEOUT = int(os.environ.get("PROXY_TIMEOUT", "1800"))


def log(msg):
    line = time.strftime("%H:%M:%S ") + msg + "\n"
    if LOG:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    else:
        sys.stderr.write(line)
        sys.stderr.flush()


def transcript(messages):
    """System à part ; une seule question utilisateur passe telle quelle,
    sinon la conversation est rendue en texte balisé."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system" and m.get("content"))
    convo = [m for m in messages if m["role"] != "system"]
    if len(convo) == 1 and convo[0]["role"] == "user":
        return system, convo[0]["content"] or ""
    parts = []
    for m in convo:
        who = {"user": "UTILISATEUR", "assistant": "ASSISTANT (ta réponse précédente)", "tool": "OUTIL"}.get(m["role"], m["role"])
        parts.append(f"<<<{who}>>>\n{m.get('content') or ''}")
    parts.append("<<<CONSIGNE>>>\nRéponds au dernier message de l'UTILISATEUR, en tenant compte de toute la conversation ci-dessus.")
    return system, "\n\n".join(parts)


def run_claude(system, prompt, schema):
    cmd = ["claude", "-p", "--model", MODEL, "--tools", "", "--strict-mcp-config",
           "--setting-sources", "", "--output-format", "json"]
    if system:
        cmd += ["--system-prompt", system]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema)]
    t0 = time.time()
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=WORKDIR, timeout=TIMEOUT)
    if p.returncode != 0:
        raise RuntimeError(f"claude -p a échoué ({p.returncode}) : {p.stderr[-2000:] or p.stdout[-2000:]}")
    out = json.loads(p.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"claude -p : {out.get('result')!r}")
    if schema is not None and out.get("structured_output") is not None:
        text = json.dumps(out["structured_output"], ensure_ascii=False)
    else:
        text = out.get("result") or ""
    u = out.get("usage", {})
    usage = {
        "prompt_tokens": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0),
        "completion_tokens": u.get("output_tokens", 0),
    }
    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    log(f"ok {time.time() - t0:.0f}s in={usage['prompt_tokens']} out={usage['completion_tokens']} cost=${out.get('total_cost_usd')}")
    return text, usage


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            return self.send_json(200, {"object": "list", "data": [
                {"id": MODEL, "object": "model", "owned_by": "claude-code", "max_model_len": 1000000}]})
        self.send_json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/chat/completions":
            return self.send_json(404, {"error": {"message": "not found"}})
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if req.get("tools"):
            log("refus : outils demandés")
            return self.send_json(400, {"error": {"message": "tools non pris en charge par ce proxy"}})
        schema = None
        rf = req.get("response_format") or {}
        if rf.get("type") == "json_schema":
            schema = rf["json_schema"]["schema"]
        system, prompt = transcript(req.get("messages", []))
        log(f"appel : {len(prompt)} car., schéma={'oui' if schema else 'non'}, stream={bool(req.get('stream'))}")
        try:
            text, usage = run_claude(system, prompt, schema)
        except Exception as e:  # noqa: BLE001
            log(f"erreur : {e}")
            return self.send_json(502, {"error": {"message": str(e)[:2000]}})
        cid = "chatcmpl-" + uuid.uuid4().hex
        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in (
                {"id": cid, "model": MODEL, "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}}]},
                {"id": cid, "model": MODEL, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {"id": cid, "model": MODEL, "choices": [], "usage": usage},
            ):
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self.send_json(200, {"id": cid, "object": "chat.completion", "model": MODEL, "usage": usage,
                             "choices": [{"index": 0, "finish_reason": "stop",
                                          "message": {"role": "assistant", "content": text}}]})


if __name__ == "__main__":
    host = os.environ.get("PROXY_HOST", "127.0.0.1")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PROXY_PORT", "18030"))
    log(f"démarrage sur {host}:{port}, modèle {MODEL}, dossier {WORKDIR}")
    try:
        ThreadingHTTPServer((host, port), Handler).serve_forever()
    except KeyboardInterrupt:
        pass
