"""Close Call. Search indexed footage and fingerprint each caption."""

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", "8080"))
VSS_URL = os.environ.get("VSS_URL", "").rstrip("/")
VSS_USERNAME = os.environ.get("VSS_USERNAME", "")
VSS_PASSWORD = os.environ.get("VSS_PASSWORD", "")
WANDB_API_KEY = os.environ.get("WANDB_API_KEY", "")
WANDB_TEAM = os.environ.get("WANDB_TEAM", "")
WANDB_PROJECT = os.environ.get("WANDB_PROJECT", "")
WANDB_MODEL = os.environ.get("WANDB_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
WANDB_BASE = os.environ.get("WANDB_BASE_URL", "https://api.inference.wandb.ai/v1").rstrip("/")

DEFAULT_QUERY = "person close to a moving forklift"
DEFAULT_CAMERA = "sdg_warehouse_cam-2"
PAGE = (Path(__file__).resolve().parent / "index.html").read_text(encoding="utf-8")

CAPTION_KEYS = ("reasoning_content", "reasoning", "caption", "description", "summary")
CAMERA_KEYS = ("camera_id",)
START_KEYS = (
    "start_time_sec",
    "start_sec",
    "start_time",
    "start",
    "best_match_start_sec",
)
END_KEYS = (
    "end_time_sec",
    "end_sec",
    "end_time",
    "end",
    "best_match_end_sec",
)
SOURCE_KEYS = ("source", "preview_source", "segment_source")
SKIP_KEYS = ("vectors", "vectors_visual", "embedding", "sql_query")


def find_key(obj, keys, depth=0):
    if depth > 5 or not isinstance(obj, dict):
        return None
    for key in keys:
        value = obj.get(key)
        if value not in (None, "", [], {}):
            return value
    for key, value in obj.items():
        if key in SKIP_KEYS or not isinstance(value, dict):
            continue
        found = find_key(value, keys, depth + 1)
        if found is not None:
            return found
    return None


def norm_time(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        number = float(value)
        if number.is_integer():
            return str(int(number))
        return ("%.3f" % number).rstrip("0").rstrip(".")
    return str(value).strip()


def fingerprint(camera, start, end, source, caption):
    raw = "\n".join([camera, start, end, source, caption])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def format_objects(payload):
    if not isinstance(payload, dict):
        return ""
    counts = find_key(payload, ("object_counts", "counts"))
    if isinstance(counts, dict) and counts:
        parts = []
        for name, count in counts.items():
            parts.append("%s %s" % (name, count))
        return ", ".join(parts)
    classes = find_key(payload, ("object_classes", "classes"))
    if isinstance(classes, list) and classes:
        return ", ".join(str(item) for item in classes)
    return ""


class VssClient(object):
    def __init__(self):
        self.token = ""

    def _request(self, method, path, payload=None, query=None, auth=True, headers=None):
        url = VSS_URL + path
        if query:
            url = url + "?" + urllib.parse.urlencode(query, doseq=True)
        data = None
        req_headers = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
        if auth:
            if not self.token:
                self.login()
            req_headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
        except urllib.error.HTTPError as err:
            if err.code == 401 and auth:
                self.login()
                req_headers["Authorization"] = "Bearer " + self.token
                request = urllib.request.Request(url, data=data, headers=req_headers, method=method)
                with urllib.request.urlopen(request, timeout=60) as response:
                    body = response.read()
            else:
                detail = err.read().decode("utf-8", errors="replace")[:400]
                raise RuntimeError("VSS %s %s failed: %s %s" % (method, path, err.code, detail))
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))

    def login(self):
        if not VSS_URL or not VSS_USERNAME or not VSS_PASSWORD:
            raise RuntimeError("VSS_URL, VSS_USERNAME, and VSS_PASSWORD are required")
        body = self._request(
            "POST",
            "/api/v1/auth/login",
            {"username": VSS_USERNAME, "password": VSS_PASSWORD},
            auth=False,
        )
        token = body.get("access_token") if isinstance(body, dict) else None
        if not token:
            raise RuntimeError("VSS login did not return an access token")
        self.token = token

    def search(self, query, camera):
        payload = {
            "query": query,
            "top_k": 8,
            "llm_top_n": 0,
            "min_similarity": 0.3,
            "time_filter": "all",
            "include_public": True,
        }
        if camera:
            payload["metadata_filters"] = {"camera_id": camera}
        try:
            return self._request("POST", "/api/v1/search", payload)
        except RuntimeError:
            payload.pop("llm_top_n", None)
            return self._request("POST", "/api/v1/search", payload)

    def metadata(self, source):
        return self._request("GET", "/api/v1/videos/metadata", query={"source": source})

    def detections(self, source):
        try:
            return self._request("GET", "/api/v1/videos/detections", query={"source": source})
        except RuntimeError:
            return {}

    def open_stream(self, source, range_header):
        if not self.token:
            self.login()
        return self._open_stream(source, range_header, retry=True)

    def _open_stream(self, source, range_header, retry):
        query = urllib.parse.urlencode(
            {"source": source, "token": self.token},
            quote_via=urllib.parse.quote,
        )
        url = VSS_URL + "/api/v1/videos/stream?" + query
        headers = {}
        if range_header:
            headers["Range"] = range_header
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            return urllib.request.urlopen(request, timeout=120)
        except urllib.error.HTTPError as err:
            if err.code == 401 and retry:
                self.login()
                return self._open_stream(source, range_header, retry=False)
            detail = err.read().decode("utf-8", errors="replace")[:200]
            raise RuntimeError("stream failed: %s %s" % (err.code, detail))


VSS = VssClient()


def card_from(hit):
    source = str(find_key(hit, SOURCE_KEYS) or "")
    caption = str(find_key(hit, CAPTION_KEYS) or "").strip()
    camera = str(find_key(hit, CAMERA_KEYS) or "")
    start = norm_time(find_key(hit, START_KEYS))
    end = norm_time(find_key(hit, END_KEYS))
    score = find_key(hit, ("similarity_score", "score"))
    return {
        "source": source,
        "caption": caption,
        "camera_id": camera,
        "start": start,
        "end": end,
        "score": score,
        "objects": format_objects(hit),
        "fingerprint": fingerprint(camera, start, end, source, caption),
    }


def judge(caption):
    if not WANDB_API_KEY or not caption:
        return "unfiltered"
    headers = {
        "Authorization": "Bearer " + WANDB_API_KEY,
        "Content-Type": "application/json",
    }
    if WANDB_TEAM and WANDB_PROJECT:
        headers["OpenAI-Project"] = WANDB_TEAM + "/" + WANDB_PROJECT
    payload = {
        "model": WANDB_MODEL,
        "temperature": 0,
        "max_tokens": 4,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You filter warehouse video captions. Reply with only keep or drop. "
                    "keep when the caption says a person is close to a moving forklift or vehicle. "
                    "drop otherwise, including when distance or motion is unknown."
                ),
            },
            {"role": "user", "content": caption[:2000]},
        ],
    }
    request = urllib.request.Request(
        WANDB_BASE + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            body = json.loads(response.read().decode("utf-8"))
        text = body["choices"][0]["message"]["content"].strip().lower()
    except (urllib.error.URLError, KeyError, IndexError, TypeError, ValueError):
        return "unfiltered"
    if text.startswith("keep"):
        return "keep"
    if text.startswith("drop"):
        return "drop"
    return "unfiltered"


def hits_of(payload):
    if not isinstance(payload, dict):
        return []
    rows = payload.get("results") or []
    if rows:
        return rows
    return payload.get("chunk_results") or []


def build_cards(query, camera):
    if not query:
        query = DEFAULT_QUERY
    relaxed = False
    payload = VSS.search(query, camera)
    rows = hits_of(payload)
    if camera and not rows:
        payload = VSS.search(query, "")
        rows = hits_of(payload)
        relaxed = True
    preliminary = []
    for row in rows:
        card = card_from(row)
        if card["source"] and card["caption"]:
            preliminary.append(card)

    def enrich(card):
        objects = format_objects(VSS.detections(card["source"]))
        if objects:
            card["objects"] = objects
        card["mark"] = judge(card["caption"])
        return card

    if not preliminary:
        return {"query": query, "camera_id": camera, "relaxed": relaxed, "cards": []}
    with ThreadPoolExecutor(max_workers=6) as pool:
        cards = list(pool.map(enrich, preliminary))
    cards.sort(key=lambda item: {"keep": 0, "unfiltered": 1, "drop": 2}.get(item["mark"], 1))
    return {"query": query, "camera_id": camera, "relaxed": relaxed, "cards": cards}


def verify_card(body):
    source = str(body.get("source") or "")
    if not source.startswith("s3://"):
        raise RuntimeError("source must be an s3 URI")
    shown_caption = str(body.get("caption") or "")
    shown_camera = str(body.get("camera_id") or "")
    shown_start = norm_time(body.get("start"))
    shown_end = norm_time(body.get("end"))
    shown_fp = str(body.get("fingerprint") or "")
    row = VSS.metadata(source)
    fresh_caption = str(find_key(row, CAPTION_KEYS) or "").strip()
    fresh_camera = str(find_key(row, CAMERA_KEYS) or shown_camera)
    fresh_start = norm_time(find_key(row, START_KEYS))
    fresh_end = norm_time(find_key(row, END_KEYS))
    if fresh_start == "":
        fresh_start = shown_start
    if fresh_end == "":
        fresh_end = shown_end
    expected = fingerprint(shown_camera, shown_start, shown_end, source, shown_caption)
    match = fresh_caption != "" and fresh_caption == shown_caption and expected == shown_fp
    return {
        "match": match,
        "fingerprint": expected,
        "caption": fresh_caption,
        "camera_id": fresh_camera,
        "start": fresh_start,
        "end": fresh_end,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _send(self, code, body, content_type):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, payload):
        self._send(code, json.dumps(payload), "application/json")

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 100000:
            raise RuntimeError("expected a small JSON body")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/health":
                self._json(200, {"ok": True})
                return
            if path in ("/", "/index.html"):
                self._send(200, PAGE, "text/html; charset=utf-8")
                return
            if path == "/api/cards":
                text = (query.get("q") or [DEFAULT_QUERY])[0].strip()[:300]
                camera = (query.get("camera") or [""])[0].strip()[:80]
                self._json(200, build_cards(text or DEFAULT_QUERY, camera))
                return
            if path == "/media":
                self._media((query.get("source") or [""])[0])
                return
        except Exception as err:
            self._json(502, {"error": str(err)})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/verify":
            self._json(404, {"error": "not found"})
            return
        try:
            self._json(200, verify_card(self._read_json()))
        except Exception as err:
            self._json(502, {"error": str(err)})

    def _media(self, source):
        if not source.startswith("s3://"):
            self._json(400, {"error": "source must be an s3 URI"})
            return
        upstream = VSS.open_stream(source, self.headers.get("Range"))
        try:
            code = upstream.status
            self.send_response(code)
            for header in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                value = upstream.headers.get(header)
                if value:
                    self.send_header(header, value)
            self.end_headers()
            while True:
                chunk = upstream.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        finally:
            upstream.close()


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
