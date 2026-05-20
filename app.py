import os
import sqlite3
import subprocess
import shutil
import zipfile
import threading
import time
import json
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from werkzeug.utils import secure_filename
from functools import wraps

app = Flask(__name__)
app.secret_key = "modx_secret_key_secure_100x"
UPLOAD_FOLDER = "uploads"
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# ----- Database Setup -----
def init_db():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    # Default login code & secret click code
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('admin_code', 'admin60')")
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('secret_click_code', '60')")
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('panel_name', 'MOD-X Unlimited Hosting')")

    c.execute('''CREATE TABLE IF NOT EXISTS servers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        main_file TEXT,
        password TEXT,
        status TEXT,
        stop_time TEXT,
        restart_time TEXT,
        created_at TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id INTEGER,
        filename TEXT,
        filepath TEXT,
        size TEXT,
        upload_date TEXT,
        FOREIGN KEY(server_id) REFERENCES servers(id)
    )''')
    
    conn.commit()
    conn.close()

init_db()

# ----- Helper Functions -----
def get_config(key):
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("SELECT value FROM config WHERE key=?", (key,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else None

def update_config(key, value):
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("UPDATE config SET value=? WHERE key=?", (value, key))
    conn.commit()
    conn.close()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ----- Routes -----
@app.route('/')
@login_required
def index():
    return render_template('index.html', panel_name=get_config('panel_name'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        code = request.form.get('code')
        admin_code = get_config('admin_code')
        if code == admin_code:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="Invalid Code")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ----- API Routes -----
@app.route('/api/servers', methods=['GET'])
@login_required
def get_servers():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("SELECT id, name, main_file, password, status, stop_time, restart_time, created_at FROM servers")
    rows = c.fetchall()
    conn.close()
    
    servers = []
    for row in rows:
        servers.append({
            'id': row[0], 'name': row[1], 'main_file': row[2],
            'password': row[3], 'status': row[4], 'stop_time': row[5],
            'restart_time': row[6], 'created_at': row[7]
        })
    return jsonify(servers)

@app.route('/api/create_server', methods=['POST'])
@login_required
def create_server():
    name = request.form.get('name')
    main_file = request.form.get('main_file', 'main.py')
    password = request.form.get('password')
    
    if not name or not password:
        return jsonify({'error': 'Name and Password required'}), 400
    
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("INSERT INTO servers (name, main_file, password, status, stop_time, restart_time, created_at) VALUES (?,?,?,?,?,?,?)",
              (name, main_file, password, "stopped", "", "", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    server_id = c.lastrowid
    conn.commit()
    conn.close()
    
    # Create folder for this server
    server_path = os.path.join(UPLOAD_FOLDER, str(server_id))
    if not os.path.exists(server_path):
        os.makedirs(server_path)
        
    return jsonify({'message': 'Server created', 'id': server_id})

@app.route('/api/upload_file/<int:server_id>', methods=['POST'])
@login_required
def upload_files(server_id):
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    server_path = os.path.join(UPLOAD_FOLDER, str(server_id))
    if not os.path.exists(server_path):
        os.makedirs(server_path)
    
    filename = secure_filename(file.filename)
    filepath = os.path.join(server_path, filename)
    file.save(filepath)
    
    # If it's a zip, extract it
    if filename.endswith('.zip'):
        with zipfile.ZipFile(filepath, 'r') as zip_ref:
            zip_ref.extractall(server_path)
        os.remove(filepath)  # remove zip after extraction
        
    # Save info in db
    file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("INSERT INTO files (server_id, filename, filepath, size, upload_date) VALUES (?,?,?,?,?)",
              (server_id, filename, filepath, str(file_size), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    
    return jsonify({'message': 'File uploaded successfully'})

@app.route('/api/files/<int:server_id>', methods=['GET'])
@login_required
def list_files(server_id):
    server_path = os.path.join(UPLOAD_FOLDER, str(server_id))
    if not os.path.exists(server_path):
        return jsonify([])
    
    files = []
    for f in os.listdir(server_path):
        full_path = os.path.join(server_path, f)
        if os.path.isfile(full_path):
            files.append({
                'name': f,
                'path': full_path,
                'size': os.path.getsize(full_path)
            })
    return jsonify(files)

@app.route('/api/download_file/<int:server_id>/<path:filename>', methods=['GET'])
@login_required
def download_file(server_id, filename):
    server_path = os.path.join(UPLOAD_FOLDER, str(server_id))
    file_path = os.path.join(server_path, filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return jsonify({'error': 'File not found'}), 404

@app.route('/api/archive_files/<int:server_id>', methods=['POST'])
@login_required
def archive_files(server_id):
    server_path = os.path.join(UPLOAD_FOLDER, str(server_id))
    zip_name = f"archive_{server_id}.zip"
    zip_path = os.path.join(UPLOAD_FOLDER, zip_name)
    
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for root, dirs, files in os.walk(server_path):
            for file in files:
                zipf.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), server_path))
    
    return send_file(zip_path, as_attachment=True, download_name=zip_name)

@app.route('/api/delete_file/<int:server_id>/<path:filename>', methods=['DELETE'])
@login_required
def delete_file(server_id, filename):
    server_path = os.path.join(UPLOAD_FOLDER, str(server_id))
    file_path = os.path.join(server_path, filename)
    if os.path.exists(file_path):
        os.remove(file_path)
        return jsonify({'message': 'Deleted'})
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/rename_file/<int:server_id>', methods=['POST'])
@login_required
def rename_file(server_id):
    old_name = request.json.get('old_name')
    new_name = request.json.get('new_name')
    server_path = os.path.join(UPLOAD_FOLDER, str(server_id))
    old_path = os.path.join(server_path, old_name)
    new_path = os.path.join(server_path, new_name)
    if os.path.exists(old_path) and not os.path.exists(new_path):
        os.rename(old_path, new_path)
        return jsonify({'message': 'Renamed'})
    return jsonify({'error': 'Invalid rename'}), 400

@app.route('/api/console/execute', methods=['POST'])
@login_required
def execute_command():
    cmd = request.json.get('command')
    if not cmd:
        return jsonify({'error': 'No command provided'}), 400
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return jsonify({'output': result.stdout + result.stderr})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/update_schedule/<int:server_id>', methods=['POST'])
@login_required
def update_schedule(server_id):
    stop_time = request.json.get('stop_time')  # HH:MM
    restart_time = request.json.get('restart_time')  # HH:MM
    
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("UPDATE servers SET stop_time=?, restart_time=? WHERE id=?", (stop_time, restart_time, server_id))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Schedule updated'})

# ----- Background Scheduler (for Auto Stop/Restart) -----
def scheduler_daemon():
    while True:
        now = datetime.now().strftime("%H:%M")
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute("SELECT id, stop_time, restart_time FROM servers")
        servers = c.fetchall()
        conn.close()
        
        for sid, stop_t, restart_t in servers:
            if stop_t and stop_t == now:
                # Trigger Auto Stop (You can implement actual process kill logic here)
                print(f"[AUTO] Stopping server {sid} at {now}")
            if restart_t and restart_t == now:
                # Trigger Auto Restart
                print(f"[AUTO] Restarting server {sid} at {now}")
        time.sleep(30)

threading.Thread(target=scheduler_daemon, daemon=True).start()

# ----- Admin Panel Routes -----
@app.route('/admin')
@login_required
def admin_panel():
    return render_template('admin.html')

@app.route('/api/admin/config', methods=['GET', 'POST'])
@login_required
def admin_config():
    if request.method == 'POST':
        data = request.json
        update_config('admin_code', data.get('admin_code'))
        update_config('secret_click_code', data.get('secret_click_code'))
        update_config('panel_name', data.get('panel_name'))
        return jsonify({'message': 'Updated'})
    else:
        return jsonify({
            'admin_code': get_config('admin_code'),
            'secret_click_code': get_config('secret_click_code'),
            'panel_name': get_config('panel_name')
        })

@app.route('/api/admin/servers_data', methods=['GET'])
@login_required
def admin_servers_data():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("SELECT id, name, password FROM servers")
    rows = c.fetchall()
    conn.close()
    return jsonify(rows)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)