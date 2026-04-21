import cv2
import numpy as np
import time
import os
import requests
import psutil
import subprocess
import json

# ================= CONFIG =================
STREAM_URL = "http://localhost:1984/api/stream.mjpeg?src=esp32"
MOONRAKER = "http://localhost:7125"

SAVE_DIR = "buffer"
EVENT_DIR = "events"

WIDTH = 640
HEIGHT = 480

TARGET_CPU_MIN = 40
TARGET_CPU_MAX = 75

# ===== DEFAULT CONFIG =====
DEFAULTS = {
    "motion_threshold": 80,
    "motion_spike": 60,
    "edge_threshold": 4500,

    "alert_cooldown": 30,

    "mask_width": 140,
    "mask_height": 100,

    "adaptive_mask": True,
    "mask_scale": 1.6,
    "mask_min": 80,
    "mask_max": 220,

    "history_size": 6,
    "fail_threshold": 3,

    "adaptive_enabled": True,
    "motion_multiplier": 6.0,
    "edge_multiplier": 1.4,
    "baseline_frames": 50,

    "startup_delay": 10,
    "alert_grace_period": 20,
    "min_baseline_samples": 20
}

def load_config():
    try:
        with open("config.json") as f:
            return json.load(f)
    except:
        return DEFAULTS

cfg = load_config()
last_cfg_reload = 0

# =========================================

os.makedirs(SAVE_DIR, exist_ok=True)
os.makedirs(EVENT_DIR, exist_ok=True)

ffmpeg_cmd = [
    "ffmpeg",
    "-loglevel", "quiet",
    "-i", STREAM_URL,
    "-vf", f"scale={WIDTH}:{HEIGHT}",
    "-f", "rawvideo",
    "-pix_fmt", "bgr24",
    "-"
]

pipe = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, bufsize=10**8)
print("[INFO] FFmpeg stream started")

# =========================================

prev_frame = None
prev_motion = 0
prev_edge = 0
frame_count = 0
fps = 2
last_alert_time = 0

nozzle_pos = None
nozzle_size = (140, 100)

history = []
motion_history = []
edge_history = []

start_time = time.time()

# ===== FUNCTIONS =====

def send_alert(frame, motion, edge):
    global last_alert_time

    cooldown = cfg.get("alert_cooldown", 30)
    if time.time() - last_alert_time < cooldown:
        return

    last_alert_time = time.time()

    print("[ALERT] Sending Telegram notification")

    ts = int(time.time() * 1000)
    img_path = f"{EVENT_DIR}/{ts}.jpg"
    cv2.imwrite(img_path, frame)

    try:
        requests.post(
            f"{MOONRAKER}/printer/gcode/script",
            json={
                "script": f'RESPOND PREFIX=tgnotify_photo MSG="⚠️ Issue! M:{int(motion)} E:{int(edge)}"'
            },
            timeout=2
        )
    except:
        print("[WARN] Telegram failed")


def adjust_load():
    global fps
    cpu = psutil.cpu_percent()

    if cpu < TARGET_CPU_MIN:
        fps = min(5, fps + 1)
    elif cpu > TARGET_CPU_MAX:
        fps = max(1, fps - 1)

    return fps


def save_frame(frame, folder):
    ts = int(time.time() * 1000)
    cv2.imwrite(f"{folder}/{ts}.jpg", frame)


def get_print_state():
    try:
        r = requests.get(f"{MOONRAKER}/printer/objects/query?print_stats", timeout=1)
        data = r.json()
        return data["result"]["status"]["print_stats"]["state"]
    except:
        return "unknown"


def detect_nozzle(prev, current):
    h, w = current.shape

    prev_s = prev[0:int(h*0.4), :]
    curr_s = current[0:int(h*0.4), :]

    diff = cv2.absdiff(prev_s, curr_s)
    _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
    thresh = cv2.dilate(thresh, None, iterations=2)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        return None, None

    c = max(contours, key=cv2.contourArea)
    if cv2.contourArea(c) < 200:
        return None, None

    x, y, w_box, h_box = cv2.boundingRect(c)
    cx = x + w_box // 2
    cy = y + h_box // 2

    return (cx, cy), (w_box, h_box)


def apply_mask(img, pos, size):
    if pos is None:
        return img

    x, y = pos

    if cfg.get("adaptive_mask", True) and size is not None:
        w = int(size[0] * cfg.get("mask_scale", 1.6))
        h = int(size[1] * cfg.get("mask_scale", 1.6))

        w = max(cfg.get("mask_min", 80), min(cfg.get("mask_max", 220), w))
        h = max(cfg.get("mask_min", 80), min(cfg.get("mask_max", 220), h))
    else:
        w = cfg.get("mask_width", 140)
        h = cfg.get("mask_height", 100)

    mask = np.ones_like(img, dtype=np.uint8) * 255

    x1 = max(0, x - w//2)
    y1 = max(0, y - h//2)
    x2 = min(img.shape[1], x + w//2)
    y2 = min(img.shape[0], y + h//2)

    mask[y1:y2, x1:x2] = 0

    return cv2.bitwise_and(img, img, mask=mask)

# =========================================

while True:
    start = time.time()

    # ===== CONFIG RELOAD =====
    if time.time() - last_cfg_reload > 5:
        cfg = load_config()
        last_cfg_reload = time.time()
        print("[INFO] config reloaded")

    raw = pipe.stdout.read(WIDTH * HEIGHT * 3)

    if len(raw) != WIDTH * HEIGHT * 3:
        print("[WARN] Frame drop")
        time.sleep(0.1)
        continue

    frame = np.frombuffer(raw, dtype=np.uint8).reshape((HEIGHT, WIDTH, 3))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    h, w = gray.shape
    roi = gray[int(h*0.2):int(h*0.9), int(w*0.2):int(w*0.8)]

    if prev_frame is None:
        prev_frame = roi
        continue

    # ===== NOZZLE TRACKING =====
    detected_pos, detected_size = detect_nozzle(prev_frame, roi)

    if detected_pos:
        if nozzle_pos is None:
            nozzle_pos = detected_pos
            nozzle_size = detected_size
        else:
            nozzle_pos = (
                int(nozzle_pos[0]*0.7 + detected_pos[0]*0.3),
                int(nozzle_pos[1]*0.7 + detected_pos[1]*0.3)
            )
            nozzle_size = (
                int(nozzle_size[0]*0.7 + detected_size[0]*0.3),
                int(nozzle_size[1]*0.7 + detected_size[1]*0.3)
            )

    roi_masked = apply_mask(roi, nozzle_pos, nozzle_size)

    # ===== STARTUP GUARD =====
    if time.time() - start_time < cfg.get("startup_delay", 10):
        print("[WARMUP] Stabilizing...")
        prev_frame = roi_masked
        continue

    # ===== MOTION =====
    diff = cv2.absdiff(prev_frame, roi_masked)
    _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

    motion = np.sum(thresh) / 255
    motion_delta = motion - prev_motion
    prev_motion = motion

    # ===== EDGE =====
    edges = cv2.Canny(roi_masked, 50, 150)
    edge = np.sum(edges) / 255
    edge_delta = edge - prev_edge
    prev_edge = edge

    # ===== ADAPTIVE BASELINE =====
    if len(motion_history) > cfg.get("baseline_frames", 50):
        motion_history.pop(0)
        edge_history.pop(0)

    motion_avg = np.mean(motion_history) if motion_history else motion
    edge_avg = np.mean(edge_history) if edge_history else edge

    adaptive_motion_thr = max(
        cfg.get("motion_threshold", 80),
        motion_avg * cfg.get("motion_multiplier", 6.0)
    )

    adaptive_edge_thr = max(
        cfg.get("edge_threshold", 4500),
        edge_avg * cfg.get("edge_multiplier", 1.4)
    )

    # ===== SCORING =====
    score = 0

    if motion > adaptive_motion_thr:
        score += 2

    if motion_delta > cfg.get("motion_spike", 60):
        score += 2

    if edge > adaptive_edge_thr or edge_delta > 800:
        score += 2

    state = get_print_state()

    if state != "printing":
        status = "IDLE"
    else:
        if score == 0:
            status = "OK"
        elif score < 4:
            status = "WARN"
        else:
            status = "FAIL"

    # ===== BASELINE UPDATE =====
    if state == "printing" and status == "OK":
        motion_history.append(motion)
        edge_history.append(edge)

    # ===== MULTI-FRAME =====
    history.append(status)
    if len(history) > cfg.get("history_size", 6):
        history.pop(0)

    fail_count = history.count("FAIL")
    confirmed = fail_count >= cfg.get("fail_threshold", 3)

    baseline_ready = len(motion_history) >= cfg.get("min_baseline_samples", 20)

    print(f"[{state.upper()}][{status}] m={motion:.0f}/{adaptive_motion_thr:.0f} e={edge:.0f}/{adaptive_edge_thr:.0f} hist={history}")

    # ===== ALERT =====
    if (
        state == "printing"
        and confirmed
        and baseline_ready
        and time.time() - start_time > cfg.get("alert_grace_period", 20)
    ):
        print(f"[CONFIRMED FAIL] {fail_count}/{len(history)}")
        send_alert(frame, motion, edge)

    # ===== STORAGE =====
    if frame_count % 2 == 0:
        save_frame(frame, SAVE_DIR)

    prev_frame = roi_masked
    frame_count += 1

    fps = adjust_load()
    time.sleep(max(0, (1.0 / fps) - (time.time() - start)))
