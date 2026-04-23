import cv2
import numpy as np
import subprocess
import threading
import time
import logging
import json
import os
from flask import Flask, jsonify, request

# ---------------- CONFIG LOAD ----------------
CONFIG_PATH = os.environ.get("KLIPPER_MON_CONFIG", "config.json")

def load_config(path=CONFIG_PATH) -> dict:
    with open(path) as f:
        return json.load(f)

def save_config(cfg: dict, path=CONFIG_PATH):
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)

cfg = load_config()

# ---------------- RUNTIME CONSTANTS (from config) ----------------
STREAM_URL       = "http://localhost:1984/api/stream.mjpeg?src=esp32"
WIDTH, HEIGHT    = 320, 240
FFMPEG_RESTART_S = 3

def _warp_px() -> int:
    return cfg.get("calibration", {}).get("warp_output_px", 512)

def _motion_fail() -> int:
    return cfg["detection"].get("motion_threshold", 150)

def _motion_warn() -> int:
    return cfg["detection"].get("motion_warn_threshold", 80)

def _edge_min() -> int:
    return cfg["detection"].get("edge_threshold", 5)

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("klipper_monitor")

# ---------------- GLOBALS (lock-protected) ----------------
_lock        = threading.Lock()
latest_frame = None
status_data  = {"motion": 0, "edge": 0, "status": "INIT"}
homography   = None   # pixel→pixel warp for display canvas

app = Flask(__name__)

# ---------------- HOMOGRAPHY HELPERS ----------------
def _build_warp_from_config() -> np.ndarray | None:
    """
    Build pixel→pixel perspective warp from config bed_points.

    Config point order (printer native coordinates):
      index 0 → xbed=0,   ybed=0    (front-left  / printer origin)
      index 1 → xbed=0,   ybed=max  (back-left)
      index 2 → xbed=max, ybed=max  (back-right)
      index 3 → xbed=max, ybed=0    (front-right)

    dst maps these to the four corners of a square WARP_PX canvas
    in the same order so the perspective is correctly un-skewed.
    """
    try:
        pts = cfg["calibration"]["bed_points"]
    except KeyError:
        return None

    if len(pts) != 4:
        log.warning("calibration.bed_points must have exactly 4 entries.")
        return None

    src = np.array([[p["px"], p["py"]] for p in pts], dtype=np.float32)

    wp = _warp_px()
    # dst corners in the same printer-coordinate order as src:
    #   origin(0,0)=front-left → canvas top-left     (0,  0 )
    #   (0,max)=back-left      → canvas bottom-left  (0,  wp)
    #   (max,max)=back-right   → canvas bottom-right (wp, wp)
    #   (max,0)=front-right    → canvas top-right    (wp, 0 )
    dst = np.array([
        [0,  0 ],
        [0,  wp],
        [wp, wp],
        [wp, 0 ],
    ], dtype=np.float32)

    return cv2.getPerspectiveTransform(src, dst)


def _build_warp_from_points(pts: list[dict]) -> np.ndarray:
    """Build pixel→pixel warp from API-supplied points (same ordering rule)."""
    src = np.array([[p["x"], p["y"]] for p in pts], dtype=np.float32)
    wp  = _warp_px()
    dst = np.array([
        [0,  0 ],
        [0,  wp],
        [wp, wp],
        [wp, 0 ],
    ], dtype=np.float32)
    return cv2.getPerspectiveTransform(src, dst)


def pixel_to_mm(px: float, py: float, matrix: np.ndarray):
    """Map a canvas pixel coordinate to printer mm using the homography."""
    pt = np.array([[[px, py]]], dtype=np.float32)
    return cv2.perspectiveTransform(pt, matrix)[0][0]


# ---------------- ROI CROP ----------------
def apply_roi(frame: np.ndarray) -> np.ndarray:
    """Crop frame to ROI fraction defined in config (if enabled)."""
    roi = cfg.get("roi", {})
    if not roi.get("enabled", False):
        return frame
    h, w = frame.shape[:2]
    x0 = int(roi.get("x_min", 0.0) * w)
    x1 = int(roi.get("x_max", 1.0) * w)
    y0 = int(roi.get("y_min", 0.0) * h)
    y1 = int(roi.get("y_max", 1.0) * h)
    return frame[y0:y1, x0:x1]


# ---------------- FFMPEG PIPE ----------------
def open_ffmpeg_pipe():
    try:
        proc = subprocess.Popen(
            [
                "ffmpeg",
                "-loglevel", "error",
                "-i", STREAM_URL,
                "-vf", f"scale={WIDTH}:{HEIGHT}",
                "-f", "rawvideo",
                "-pix_fmt", "bgr24",
                "-"
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=WIDTH * HEIGHT * 3 * 4,
        )
        log.info("FFmpeg pipe opened.")
        return proc
    except FileNotFoundError:
        log.error("ffmpeg not found — sudo apt install ffmpeg")
        return None


# ---------------- AI LOOP ----------------
def ai_loop():
    global latest_frame, status_data, homography

    # Load homography from config on startup
    with _lock:
        homography = _build_warp_from_config()
        if homography is not None:
            log.info("Homography loaded from config.json.")
        else:
            log.warning("No valid calibration in config — running without perspective correction.")

    pipe      = None
    prev_gray = None
    frame_bytes = WIDTH * HEIGHT * 3

    log.info("AI loop started.")

    while True:
        # --- keep pipe alive ---
        if pipe is None or pipe.poll() is not None:
            if pipe is not None:
                log.warning("FFmpeg pipe died. Restarting in %ds…", FFMPEG_RESTART_S)
                time.sleep(FFMPEG_RESTART_S)
            pipe = open_ffmpeg_pipe()
            prev_gray = None
            if pipe is None:
                time.sleep(FFMPEG_RESTART_S)
                continue

        # --- read one raw frame ---
        raw = pipe.stdout.read(frame_bytes)
        if len(raw) != frame_bytes:
            pipe.kill()
            pipe = None
            continue

        frame = np.frombuffer(raw, np.uint8).reshape((HEIGHT, WIDTH, 3))

        # --- perspective correction ---
        with _lock:
            h = homography
        if h is not None:
            wp    = _warp_px()
            frame = cv2.warpPerspective(frame, h, (wp, wp))

        # --- ROI crop (applied after warp so coords are in bed space) ---
        frame = apply_roi(frame)

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # --- motion ---
        motion = 0
        if prev_gray is not None:
            if prev_gray.shape != gray.shape:
                prev_gray = cv2.resize(prev_gray, (gray.shape[1], gray.shape[0]))
            diff   = cv2.absdiff(prev_gray, gray)
            motion = int(np.sum(diff.astype(np.int32)) // 1000)
        prev_gray = gray

        # --- edges ---
        edges      = cv2.Canny(gray, 100, 200)
        edge_score = int(np.sum(edges) // 1000)

        # --- thresholds from config (hot-reload friendly) ---
        if motion > _motion_fail():
            status = "FAIL"
        elif motion > _motion_warn():
            status = "WARN"
        elif edge_score < _edge_min():
            status = "BLIND"
        else:
            status = "OK"

        with _lock:
            latest_frame = frame.copy()
            status_data  = {
                "motion": motion,
                "edge":   edge_score,
                "status": status,
            }

        if cfg["debug"].get("print_scores", False):
            log.info("%-5s | M:%-6d E:%d", status, motion, edge_score)


# ---------------- API ----------------

@app.route("/status")
def route_status():
    with _lock:
        return jsonify(status_data)


@app.route("/calibrate", methods=["POST"])
def route_calibrate():
    """
    POST JSON with 4 pixel corners in printer-coordinate order:
    {
        "points": [
            {"x": 520, "y": 380},   ← index 0: printer origin  (xbed=0,   ybed=0)
            {"x": 520, "y": 100},   ← index 1: back-left       (xbed=0,   ybed=max)
            {"x": 100, "y": 100},   ← index 2: back-right      (xbed=max, ybed=max)
            {"x": 100, "y": 380}    ← index 3: front-right     (xbed=max, ybed=0)
        ],
        "save": true   ← optional: persist to config.json
    }
    """
    global homography

    data = request.get_json(silent=True)
    if not data or "points" not in data:
        return jsonify({"ok": False, "error": "Missing 'points' key"}), 400

    pts = data["points"]
    if len(pts) != 4:
        return jsonify({"ok": False, "error": "Exactly 4 points required"}), 400

    try:
        h = _build_warp_from_points(pts)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    with _lock:
        homography = h

    # optionally persist to config.json
    if data.get("save", False):
        cfg["calibration"]["bed_points"] = [
            {"label": "origin_fl", "px": pts[0]["x"], "py": pts[0]["y"]},
            {"label": "back_l",    "px": pts[1]["x"], "py": pts[1]["y"]},
            {"label": "back_r",    "px": pts[2]["x"], "py": pts[2]["y"]},
            {"label": "origin_fr", "px": pts[3]["x"], "py": pts[3]["y"]},
        ]
        save_config(cfg)
        log.info("Calibration saved to config.json.")

    log.info("Homography updated via API.")
    return jsonify({"ok": True})


@app.route("/config", methods=["GET"])
def route_config_get():
    """Return current runtime config (minus internal notes)."""
    safe = {k: v for k, v in cfg.items() if not k.startswith("_")}
    return jsonify(safe)


@app.route("/config", methods=["PATCH"])
def route_config_patch():
    """
    Hot-patch any top-level config section without restart.
    Example: PATCH /config  {"detection": {"motion_threshold": 200}}
    Nested keys are merged, not replaced.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": False, "error": "No JSON body"}), 400

    for section, values in data.items():
        if section not in cfg:
            cfg[section] = {}
        if isinstance(values, dict):
            cfg[section].update(values)
        else:
            cfg[section] = values

    save_config(cfg)
    log.info("Config patched: %s", list(data.keys()))
    return jsonify({"ok": True, "updated": list(data.keys())})


# ---------------- START ----------------
if __name__ == "__main__":
    threading.Thread(target=ai_loop, daemon=True).start()
    log.info("API running on port 5001")
    app.run(host="0.0.0.0", port=5001, threaded=True)
