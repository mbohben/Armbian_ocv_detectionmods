from flask import Flask, send_file, render_template_string
import os

app = Flask(__name__)
DEBUG_PATH = "/tmp/ai_debug.jpg"

@app.route('/')
def index():
    return render_template_string("""
    <html>
        <body style="background:#000; color:#fff; text-align:center;">
            <h2>Pit Crew AI Monitor</h2>
            <img id="stream" src="/debug" style="width:80%; border:2px solid #333;">
            <script>
                setInterval(function(){
                    document.getElementById('stream').src = "/debug?t=" + new Date().getTime();
                }, 200);
            </script>
        </body>
    </html>
    """)

@app.route('/debug')
def debug():
    if os.path.exists(DEBUG_PATH):
        # mimetype='image/jpeg' tells the browser to show an image, not text
        return send_file(DEBUG_PATH, mimetype='image/jpeg', cache_timeout=0)
    return "No Frame", 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
