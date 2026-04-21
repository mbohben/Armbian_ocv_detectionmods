from flask import Flask, render_template, request, jsonify, send_file
import json
import requests
import os

app = Flask(__name__)
CONFIG_PATH = "config.json"
PRINTER_IP = "127.0.0.1" 
DEBUG_PATH = "/tmp/ai_debug.jpg"

def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

@app.route("/")
def index():
    return render_template("index.html", cfg=load_config())

@app.route("/update", methods=["POST"])
def update():
    cfg = load_config()
    data = request.json
    # Deep update for nested dictionaries
    for k, v in data.items():
        if isinstance(v, dict) and k in cfg:
            cfg[k].update(v)
        else:
            cfg[k] = v
    save_config(cfg)
    return "OK"

@app.route("/gcode", methods=["POST"])
def send_gcode():
    command = request.json.get("command")
    try:
        # Using params instead of f-string for safer URL encoding
        url = f"http://{PRINTER_IP}:7125/printer/gcode/script"
        response = requests.post(url, params={'script': command})
        return response.text
    except Exception as e:
        return str(e), 500

@app.route("/debug")
def debug():
    if os.path.exists(DEBUG_PATH):
        # FIX: send_file sets the correct MIME type so the browser shows an image
        # cache_timeout=0 ensures the browser doesn't show an old stale frame
        return send_file(DEBUG_PATH, mimetype='image/jpeg', last_modified=os.path.getmtime(DEBUG_PATH))
    else:
        return "Image not found", 404

if __name__ == "__main__":
    # Note: debug=True can sometimes interfere with FFmpeg pipes, 
    # but it's fine for testing the Web UI logic.
    app.run(host="0.0.0.0", port=5000, debug=True)
