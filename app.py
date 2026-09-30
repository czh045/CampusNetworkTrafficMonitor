from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
from predictor import TrafficPredictor
from config import Config, get_db_connection
import pymysql
from datetime import datetime, timedelta
import threading
import time
import requests
import hashlib
import hmac
import base64
import urllib.parse
import ipaddress
import logging
from functools import wraps

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = app.config["SECRET_KEY"]
logger = logging.getLogger(__name__)

# ==================== 数据库连接 ====================
def get_db():
    return get_db_connection()


DINGTALK_ACCESS_TOKEN = app.config["DINGTALK_ACCESS_TOKEN"]
DINGTALK_SECRET = app.config["DINGTALK_SECRET"]
DINGTALK_WEBHOOK_BASE = 'https://oapi.dingtalk.com/robot/send'
ALERT_NOTIFY_INTERVAL_MINUTES = app.config["ALERT_NOTIFY_INTERVAL_MINUTES"]
ALERT_NOTIFY_MAX_COUNT = app.config["ALERT_NOTIFY_MAX_COUNT"]


def _access_error(error, status_code):
    if request.path.startswith("/api/"):
        return jsonify({"error": error}), status_code
    return redirect("/show/infer")


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("user_id"):
            return _access_error("authentication_required", 401)
        return view(*args, **kwargs)
    return wrapped_view


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("user_id"):
            return _access_error("authentication_required", 401)
        if int(session.get("role_id", 0)) != 1:
            return _access_error("administrator_required", 403)
        return view(*args, **kwargs)
    return wrapped_view


def build_dingtalk_webhook():
    """构建钉钉机器人完整 webhook URL，支持安全签名"""
    if not DINGTALK_ACCESS_TOKEN:
        return None

    params = {'access_token': DINGTALK_ACCESS_TOKEN}
    if DINGTALK_SECRET:
        timestamp = str(int(time.time() * 1000))
        secret_bytes = DINGTALK_SECRET.encode('utf-8')
        string_to_sign = f'{timestamp}\n{DINGTALK_SECRET}'.encode('utf-8')
        hmac_code = hmac.new(secret_bytes, string_to_sign, digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code).decode('utf-8'))
        params['timestamp'] = timestamp
        params['sign'] = sign
    query = '&'.join([f'{k}={v}' for k, v in params.items()])
    return f'{DINGTALK_WEBHOOK_BASE}?{query}'


def send_dingtalk_message(content):
    """向钉钉机器人发送文本通知"""
    webhook = build_dingtalk_webhook()
    if not webhook:
        logger.info("DingTalk is not configured; notification skipped.")
        return False

    headers = {'Content-Type': 'application/json;charset=utf-8'}
    payload = {
        'msgtype': 'text',
        'text': {
            'content': content
        }
    }
    try:
        resp = requests.post(webhook, json=payload, headers=headers, timeout=6)
        data = resp.json()
        if resp.status_code == 200 and data.get('errcode') == 0:
            logger.info("DingTalk notification sent.")
            return True
        logger.warning("DingTalk notification failed with status %s.", resp.status_code)
    except requests.RequestException:
        logger.exception("DingTalk notification request failed.")
    return False


def ensure_alert_notification_fields(cursor):
    """确保 alerts 表存在通知字段"""
    cursor.execute("SHOW COLUMNS FROM alerts LIKE 'last_notified_at'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE alerts ADD COLUMN last_notified_at DATETIME NULL")
    cursor.execute("SHOW COLUMNS FROM alerts LIKE 'notify_count'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE alerts ADD COLUMN notify_count INT DEFAULT 0")


def notify_critical_alert(alert_id, title, message, cursor):
    """发送严重告警到钉钉并记录通知时间"""
    content = f"[严重告警] {title}\n{message}\n请尽快处理。"
    if send_dingtalk_message(content):
        cursor.execute(
            "UPDATE alerts SET last_notified_at = NOW(), notify_count = COALESCE(notify_count, 0) + 1 WHERE id = %s",
            (alert_id,)
        )


def add_log(user_id, action_type, message, log_level='INFO', ip_address=None):
    """记录用户操作日志"""
    if ip_address is None:
        from flask import request
        ip_address = request.remote_addr if hasattr(request, 'remote_addr') else '127.0.0.1'

    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO system_logs (user_id, log_level, action_type, message, ip_address, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
        """, (user_id, log_level, action_type, message, ip_address))
        conn.commit()
    except pymysql.MySQLError:
        logger.exception("Unable to write system log.")
    finally:
        cursor.close()
        conn.close()

# ==================== 用户认证模块 ====================
@app.route('/')
def index():
    return redirect('/show/infer')

@app.route('/show/infer', methods=['GET', 'POST'])
def show_infer():
    if request.method == "GET":
        return render_template("index.html")

    username = (request.form.get('name') or '').strip()
    password = request.form.get('password') or ''
    email = (request.form.get('email') or '').strip()

    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    try:
        cursor.execute("SELECT * FROM sys_user WHERE username = %s", (username,))
        row = cursor.fetchone()
        valid_login = (
            row
            and row.get('is_active')
            and check_password_hash(row['password_hash'], password)
            and (not email or row['email'] == email)
        )
        if not valid_login:
            return redirect('/get/wrong')

        cursor.execute("UPDATE sys_user SET last_login = NOW() WHERE id = %s", (row['id'],))
        conn.commit()
        session['user_id'] = row['id']
        session['username'] = row['username']
        session['role_id'] = row['role_id']
    finally:
        cursor.close()
        conn.close()

    add_log(row['id'], 'login', f'User {username} logged in', 'INFO')
    if row['role_id'] == 1:
        return redirect('/get/manager')
    return redirect('/get/user')



@app.route('/get/register', methods=['GET', 'POST'])
def get_register():
    if request.method == "GET":
        return render_template("register.html")

    username = (request.form.get('u') or '').strip()
    password = request.form.get('p') or ''
    email = (request.form.get('e') or '').strip().lower()
    role = app.config["DEFAULT_USER_ROLE"]

    if not all([username, password, email]):
        return "Please complete every field.", 400
    if not 3 <= len(username) <= 64 or len(password) < 8 or "@" not in email:
        return "Use a 3-64 character username, an 8+ character password, and a valid email.", 400

    conn = get_db()
    cursor = conn.cursor()
    try:
        password_hash = generate_password_hash(password)
        cursor.execute(
            "INSERT INTO sys_user (username, password_hash, email, role_id, is_active) VALUES (%s, %s, %s, %s, %s)",
            (username, password_hash, email, role, 1)
        )
        conn.commit()
        user_id = cursor.lastrowid
        add_log(user_id, 'register', f'新用户注册: {username}', 'INFO')
        return redirect('/show/infer')
    except pymysql.IntegrityError:
        return "Username or email already exists.", 409
    except pymysql.MySQLError:
        logger.exception("Registration failed.")
        return "Registration failed. Please try again later.", 500
    finally:
        cursor.close()
        conn.close()

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/show/infer')


@app.route('/api/online/count')
@login_required
def get_online_count():
    """统计在线用户数（5分钟内有活动的用户）"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) FROM sys_user
        WHERE last_login IS NOT NULL
        AND last_login >= DATE_SUB(NOW(), INTERVAL 5 MINUTE)
    """)
    online_count = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return jsonify({'online_count': online_count})

# ==================== 实时监控模块 ====================
@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

@app.route('/api/traffic/now')
@login_required
def get_traffic_now():
    try:
        conn = get_db()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        cursor.execute("""
            SELECT
                d.id,
                d.device_name,
                d.ip_address,
                t.in_rate,
                t.out_rate
            FROM mon_device d
            LEFT JOIN (
                SELECT device_id, MAX(timestamp) as max_time
                FROM realtime_traffic
                GROUP BY device_id
            ) latest ON d.id = latest.device_id
            LEFT JOIN realtime_traffic t ON
                t.device_id = latest.device_id AND
                t.timestamp = latest.max_time
            WHERE d.status = 1
            ORDER BY d.id
        """)

        devices = cursor.fetchall()

        total_in = 0.0
        total_out = 0.0
        online_count = 0

        for d in devices:
            in_rate = float(d['in_rate'] if d['in_rate'] is not None else 0)
            out_rate = float(d['out_rate'] if d['out_rate'] is not None else 0)
            total_in += in_rate
            total_out += out_rate
            if in_rate > 0 or out_rate > 0:
                online_count += 1

        cursor.execute("""
            SELECT ROUND(MAX(total_in + total_out), 2) AS peak_traffic
            FROM (
                SELECT SUM(in_rate) AS total_in, SUM(out_rate) AS total_out
                FROM realtime_traffic
                WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 10 MINUTE)
                GROUP BY timestamp
            ) t
        """)
        peak = cursor.fetchone()
        cursor.close()
        conn.close()

        return jsonify({
            'total_in': round(total_in, 2),
            'total_out': round(total_out, 2),
            'peak_traffic': float(peak['peak_traffic'] or 0),
            'online_count': online_count,
            'devices': devices
        })

    except pymysql.MySQLError:
        logger.exception("Unable to load current traffic.")
        return jsonify({'error': 'traffic_data_unavailable', 'total_in': 0, 'total_out': 0, 'online_count': 0, 'devices': []}), 503

@app.route('/api/traffic/history')
@login_required
def get_traffic_history():
    """获取历史流量数据（用于图表）"""
    try:
        conn = get_db()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        cursor.execute("""
            SELECT
                timestamp,
                ROUND(SUM(in_rate), 2) as total_in,
                ROUND(SUM(out_rate), 2) as total_out
            FROM realtime_traffic
            WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 10 MINUTE)
            GROUP BY timestamp
            ORDER BY timestamp DESC
            LIMIT 30
        """)

        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        if not rows:
            return jsonify([])

        rows.reverse()

        result = []
        for row in rows:
            time_str = row['timestamp'].strftime('%M:%S')
            result.append({
                'time': time_str,
                'avg_in': row['total_in'],
                'avg_out': row['total_out']
            })

        return jsonify(result)

    except pymysql.MySQLError:
        logger.exception("Unable to load traffic history.")
        return jsonify([]), 503
# ==================== 流量分析模块 ====================
@app.route('/traffic/analysis')
@login_required
def traffic_analysis():
    return render_template('traffic_analysis.html')

@app.route('/api/analysis/traffic')
@login_required
def get_traffic_analysis():
    period = request.args.get('period', 'day')
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    if period == 'day':
        cursor.execute("""
            SELECT HOUR(timestamp) as hour,
                   ROUND(AVG(in_rate), 2) as avg_in,
                   ROUND(AVG(out_rate), 2) as avg_out
            FROM realtime_traffic
            WHERE DATE(timestamp) = CURDATE()
            GROUP BY HOUR(timestamp)
            ORDER BY hour
        """)
        data = cursor.fetchall()
        result = [{'time': f"{d['hour']:02d}:00", 'avg_in': d['avg_in'] or 0, 'avg_out': d['avg_out'] or 0} for d in data]

    elif period == 'week':
        cursor.execute("""
            SELECT DATE(timestamp) as date,
                   ROUND(AVG(in_rate), 2) as avg_in,
                   ROUND(AVG(out_rate), 2) as avg_out
            FROM realtime_traffic
            WHERE timestamp >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
            GROUP BY DATE(timestamp)
            ORDER BY date
        """)
        data = cursor.fetchall()
        result = [{'time': d['date'].strftime('%m-%d'), 'avg_in': d['avg_in'] or 0, 'avg_out': d['avg_out'] or 0} for d in data]

    else:
        cursor.execute("""
            SELECT DATE(timestamp) as date,
                   ROUND(AVG(in_rate), 2) as avg_in,
                   ROUND(AVG(out_rate), 2) as avg_out
            FROM realtime_traffic
            WHERE timestamp >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY DATE(timestamp)
            ORDER BY date
        """)
        data = cursor.fetchall()
        result = [{'time': d['date'].strftime('%m-%d'), 'avg_in': d['avg_in'] or 0, 'avg_out': d['avg_out'] or 0} for d in data]

    cursor.close()
    conn.close()
    return jsonify(result)

@app.route('/api/analysis/top-devices')
@login_required
def get_top_devices():
    period = request.args.get('period', 'hour')
    limit = min(max(request.args.get('limit', 10, type=int), 1), 100)

    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    if period == 'hour':
        cursor.execute("""
            SELECT d.device_name, d.ip_address,
                   ROUND(AVG(t.in_rate), 2) as avg_in,
                   ROUND(AVG(t.out_rate), 2) as avg_out,
                   ROUND(MAX(t.in_rate), 2) as max_in,
                   ROUND(SUM(t.in_rate * 125000 * 5) / 1e9, 2) as total_gb
            FROM realtime_traffic t
            JOIN mon_device d ON t.device_id = d.id
            WHERE t.timestamp >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
            GROUP BY d.id
            ORDER BY avg_in DESC
            LIMIT %s
        """, (limit,))
    else:
        cursor.execute("""
            SELECT d.device_name, d.ip_address,
                   ROUND(AVG(t.in_rate), 2) as avg_in,
                   ROUND(AVG(t.out_rate), 2) as avg_out,
                   ROUND(MAX(t.in_rate), 2) as max_in,
                   ROUND(SUM(t.in_rate * 125000 * 5) / 1e9, 2) as total_gb
            FROM realtime_traffic t
            JOIN mon_device d ON t.device_id = d.id
            WHERE DATE(t.timestamp) = CURDATE()
            GROUP BY d.id
            ORDER BY avg_in DESC
            LIMIT %s
        """, (limit,))

    devices = cursor.fetchall()
    cursor.close()
    conn.close()

    for i, d in enumerate(devices, 1):
        d['rank'] = i
    return jsonify(devices)

@app.route('/api/analysis/protocol-stats')
@login_required
def get_protocol_stats():
    """Return recent TCP/UDP activity derived from stored telemetry, not random data."""
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    try:
        cursor.execute("""
            SELECT
                COALESCE(SUM(tcp_connections), 0) AS tcp_connections,
                COALESCE(SUM(udp_flows), 0) AS udp_flows
            FROM realtime_traffic
            WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
        """)
        totals = cursor.fetchone()
        tcp = int(totals['tcp_connections'])
        udp = int(totals['udp_flows'])
        return jsonify([
            {'name': 'TCP connections', 'value': tcp},
            {'name': 'UDP flows', 'value': udp}
        ])
    finally:
        cursor.close()
        conn.close()

@app.route('/api/analysis/traffic-summary')
@login_required
def get_traffic_summary():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT ROUND(SUM(in_rate * 125000 * 5) / 1e9, 2) as today_gb,
               ROUND(AVG(in_rate), 2) as avg_in_today,
               ROUND(AVG(out_rate), 2) as avg_out_today
        FROM realtime_traffic
        WHERE DATE(timestamp) = CURDATE()
    """)
    today = cursor.fetchone()

    cursor.execute("""
        SELECT ROUND(MAX(in_rate), 2) as peak_in,
               ROUND(MAX(out_rate), 2) as peak_out
        FROM realtime_traffic
        WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    """)
    peak = cursor.fetchone()

    cursor.execute("SELECT COUNT(*) FROM mon_device WHERE status=1")
    device_count = cursor.fetchone()[0]

    cursor.close()
    conn.close()

    return jsonify({
        'today_total_gb': today[0] or 0,
        'avg_in_today': today[1] or 0,
        'avg_out_today': today[2] or 0,
        'peak_in_24h': peak[0] or 0,
        'peak_out_24h': peak[1] or 0,
        'total_devices': device_count
    })

# ==================== 流量预测模块 ====================
predictor = None


def get_predictor():
    """Load a local model lazily so importing the Flask app does not touch MySQL."""
    global predictor
    if predictor is None:
        predictor = TrafficPredictor()
        predictor.load_model()
    return predictor

@app.route('/api/predict/traffic')
@login_required
def predict_traffic():
    """获取流量预测"""
    result = get_predictor().predict_next(24)
    if result:
        return jsonify(result)
    return jsonify({'error': '数据不足，无法预测', 'predictions': []})

@app.route('/api/predict/metrics')
@login_required
def get_predict_metrics():
    """获取预测模型评估指标"""
    result = get_predictor().get_model_metrics()
    return jsonify(result)

@app.route('/api/predict/anomaly')
@login_required
def get_anomaly():
    """获取当前流量异常分数"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT AVG(in_rate) as avg_in
        FROM realtime_traffic
        WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 5 MINUTE)
    """)
    result = cursor.fetchone()
    cursor.close()
    conn.close()

    current_value = result[0] if result else 0
    score = get_predictor().get_anomaly_score(current_value)

    level = 'normal'
    if score > 80:
        level = 'critical'
    elif score > 60:
        level = 'warning'
    elif score > 40:
        level = 'attention'

    return jsonify({
        'current_value': round(current_value, 2),
        'anomaly_score': round(score, 2),
        'level': level
    })

@app.route('/api/predict/train', methods=['POST'])
@admin_required
def train_prediction_model():
    """手动训练模型"""
    success = get_predictor().train_model()
    return jsonify({'success': success, 'message': '模型训练完成' if success else '训练失败'})

def check_alerts():
    """定时检测流量数据，自动生成告警"""
    try:
        conn = get_db()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        ensure_alert_notification_fields(cursor)

        # 修复后的SQL
        cursor.execute("""
            SELECT
                t.device_id,
                d.device_name,
                MAX(t.in_rate) as in_rate,
                MAX(t.tcp_connections) as tcp_connections,
                MAX(t.packet_loss) as packet_loss,
                MAX(t.timestamp) as timestamp
            FROM realtime_traffic t
            JOIN mon_device d ON t.device_id = d.id
            WHERE t.timestamp >= DATE_SUB(NOW(), INTERVAL 10 SECOND)
            GROUP BY t.device_id, d.device_name
            ORDER BY timestamp DESC
        """)

        devices = cursor.fetchall()

        for device in devices:
            device_id = device['device_id']
            device_name = device['device_name']
            in_rate = device['in_rate'] or 0
            tcp_conn = device['tcp_connections'] or 0
            packet_loss = device['packet_loss'] or 0

            # 1. 流量告警（阈值80）
            if in_rate > 80:
                severity = 'critical' if in_rate > 120 else 'warning'
                title = f"{device_name} 流量超阈值"
                message = f"{device_name} 入流量达到 {in_rate} Mbps"

                cursor.execute("""
                    SELECT id FROM alerts
                    WHERE device_id = %s AND alert_type = 'traffic'
                    AND status = 'active'
                    AND created_at >= DATE_SUB(NOW(), INTERVAL 5 MINUTE)
                """, (device_id,))

                if not cursor.fetchone():
                    cursor.execute("""
                        INSERT INTO alerts (device_id, alert_type, severity, title, message, current_value, threshold_value, status)
                        VALUES (%s, 'traffic', %s, %s, %s, %s, 80, 'active')
                    """, (device_id, severity, title, message, in_rate))
                    alert_id = cursor.lastrowid
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 生成流量告警: {device_name} ({in_rate}Mbps)")
                    if severity == 'critical':
                        notify_critical_alert(alert_id, title, message, cursor)

            # 2. TCP连接数告警（阈值2000）
            if tcp_conn > 2000:
                cursor.execute("""
                    SELECT id FROM alerts
                    WHERE device_id = %s AND alert_type = 'connection'
                    AND status = 'active'
                    AND created_at >= DATE_SUB(NOW(), INTERVAL 10 MINUTE)
                """, (device_id,))

                if not cursor.fetchone():
                    cursor.execute("""
                        INSERT INTO alerts (device_id, alert_type, severity, title, message, current_value, threshold_value, status)
                        VALUES (%s, 'connection', 'warning', %s, %s, %s, 2000, 'active')
                    """, (device_id, f"{device_name} TCP连接数异常", f"{device_name} TCP连接数达到 {tcp_conn}", tcp_conn))
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 生成连接数告警: {device_name} ({tcp_conn})")

            # 3. 丢包率告警（阈值5%）
            if packet_loss > 5:
                cursor.execute("""
                    SELECT id FROM alerts
                    WHERE device_id = %s AND alert_type = 'packet_loss'
                    AND status = 'active'
                    AND created_at >= DATE_SUB(NOW(), INTERVAL 15 MINUTE)
                """, (device_id,))

                if not cursor.fetchone():
                    cursor.execute("""
                        INSERT INTO alerts (device_id, alert_type, severity, title, message, current_value, threshold_value, status)
                        VALUES (%s, 'packet_loss', 'critical', %s, %s, %s, 5, 'active')
                    """, (device_id, f"{device_name} 丢包率异常", f"{device_name} 丢包率达到 {packet_loss}%", packet_loss))
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 生成丢包告警: {device_name} ({packet_loss}%)")

        # 对现存严重未解决告警执行周期性提醒
        cursor.execute("""
            SELECT id, title, message, notify_count FROM alerts
            WHERE severity = 'critical'
              AND status IN ('active', 'acknowledged')
              AND (last_notified_at IS NULL OR last_notified_at <= DATE_SUB(NOW(), INTERVAL %s MINUTE))
              AND COALESCE(notify_count, 0) < %s
        """, (ALERT_NOTIFY_INTERVAL_MINUTES, ALERT_NOTIFY_MAX_COUNT))
        pending = cursor.fetchall()
        for row in pending:
            notify_critical_alert(row['id'], row['title'], row['message'], cursor)

        conn.commit()
        cursor.close()
        conn.close()

    except Exception as e:
        print(f"告警检测错误: {e}")

def start_alert_checker():
    """启动告警检测定时任务"""
    def run():
        while True:
            check_alerts()
            time.sleep(10)  # 每10秒检测一次

    thread = threading.Thread(target=run)
    thread.daemon = True
    thread.start()

# ==================== 告警中心模块 ====================
@app.route('/alerts')
@login_required
def alerts():
    if 'user_id' not in session:
        return redirect('/show/infer')
    return render_template('alerts.html')

@app.route('/api/alerts/list')
@login_required
def get_alerts_list():
    status = request.args.get('status', 'all')
    severity = request.args.get('severity', 'all')

    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    query = """
        SELECT a.*, d.device_name, d.ip_address
        FROM alerts a
        LEFT JOIN mon_device d ON a.device_id = d.id
        WHERE 1=1
    """
    params = []
    if status != 'all':
        query += " AND a.status = %s"
        params.append(status)
    if severity != 'all':
        query += " AND a.severity = %s"
        params.append(severity)
    query += " ORDER BY a.created_at DESC LIMIT 100"

    cursor.execute(query, params)
    alerts = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(alerts)

@app.route('/api/alerts/statistics')
@login_required
def get_alerts_statistics():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM alerts WHERE status = 'active'")
    active = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM alerts WHERE status = 'resolved'")
    resolved = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM alerts WHERE severity = 'critical'")
    critical = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM alerts WHERE severity = 'warning'")
    warning = cursor.fetchone()[0]

    cursor.close()
    conn.close()

    return jsonify({
        'total': active + resolved,
        'active': active,
        'resolved': resolved,
        'critical': critical,
        'warning': warning
    })

@app.route('/api/alerts/ack/<int:alert_id>', methods=['POST'])
@admin_required
def acknowledge_alert(alert_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE alerts SET status='acknowledged' WHERE id=%s", (alert_id,))
    conn.commit()
    add_log(session.get('user_id'), 'alert_ack', f'确认告警 ID: {alert_id}', 'WARNING')
    cursor.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/alerts/resolve/<int:alert_id>', methods=['POST'])
@admin_required
def resolve_alert(alert_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE alerts SET status='resolved', resolved_at=NOW() WHERE id=%s", (alert_id,))
    conn.commit()
    add_log(session.get('user_id'), 'alert_resolve', f'解决告警 ID: {alert_id}', 'INFO')
    cursor.close()
    conn.close()
    return jsonify({'success': True})

# ==================== 设备管理模块 ====================
def validate_device_payload(data, require_status=False):
    """Validate and normalize device input before it reaches SQL."""
    if not isinstance(data, dict):
        return 'invalid_device_payload'

    device_name = str(data.get('device_name', '')).strip()
    ip_address = str(data.get('ip_address', '')).strip()
    if not 1 <= len(device_name) <= 100:
        return 'invalid_device_name'
    try:
        ipaddress.ip_address(ip_address)
    except ValueError:
        return 'invalid_ip_address'

    data['device_name'] = device_name
    data['ip_address'] = ip_address
    if require_status:
        if str(data.get('status')) not in {'0', '1'}:
            return 'invalid_device_status'
        data['status'] = int(data['status'])
    return None


@app.route('/devices')
@admin_required
def devices():
    if 'user_id' not in session or session.get('role_id') != 1:
        return redirect('/show/infer')
    return render_template('devices.html')

@app.route('/api/devices/list')
@admin_required
def get_devices_list():
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    cursor.execute("""
        SELECT d.id, d.device_name, d.ip_address, d.device_type, d.status,
               d.last_check, d.created_at,
               (SELECT AVG(in_rate) FROM realtime_traffic
                WHERE device_id = d.id AND timestamp >= DATE_SUB(NOW(), INTERVAL 1 HOUR)) as avg_in_rate
        FROM mon_device d
        ORDER BY d.id
    """)

    devices = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(devices)

@app.route('/api/devices/add', methods=['POST'])
@admin_required
def add_device():
    data = request.get_json(silent=True) or {}
    error = validate_device_payload(data)
    if error:
        return jsonify({'success': False, 'error': error}), 400
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO mon_device (device_name, ip_address, device_type, status)
            VALUES (%s, %s, %s, 1)
        """, (data['device_name'], data['ip_address'], data.get('device_type')))
        conn.commit()
        add_log(session.get('user_id'), 'device_add', f'添加设备: {data["device_name"]}', 'INFO')
        return jsonify({'success': True, 'id': cursor.lastrowid})
    except pymysql.MySQLError:
        logger.exception("Unable to add device.")
        return jsonify({'success': False, 'error': 'device_create_failed'}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/devices/update/<int:device_id>', methods=['POST'])
@admin_required
def update_device(device_id):
    data = request.get_json(silent=True) or {}
    error = validate_device_payload(data, require_status=True)
    if error:
        return jsonify({'success': False, 'error': error}), 400
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE mon_device
            SET device_name=%s, ip_address=%s, device_type=%s, status=%s
            WHERE id=%s
        """, (data['device_name'], data['ip_address'], data.get('device_type'), data['status'], device_id))
        conn.commit()
        add_log(session.get('user_id'), 'device_edit', f'编辑设备: {data["device_name"]}', 'INFO')
        return jsonify({'success': True})
    except pymysql.MySQLError:
        logger.exception("Unable to update device %s.", device_id)
        return jsonify({'success': False, 'error': 'device_update_failed'}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/devices/delete/<int:device_id>', methods=['POST'])
@admin_required
def delete_device(device_id):
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT device_name FROM mon_device WHERE id=%s", (device_id,))
        result = cursor.fetchone()
        device_name = result[0] if result else str(device_id)

        cursor.execute("DELETE FROM mon_device WHERE id=%s", (device_id,))
        conn.commit()
        add_log(session.get('user_id'), 'device_delete', f'删除设备: {device_name}', 'WARNING')
        return jsonify({'success': True})
    except pymysql.MySQLError:
        logger.exception("Unable to delete device %s.", device_id)
        return jsonify({'success': False, 'error': 'device_delete_failed'}), 500
    finally:
        cursor.close()
        conn.close()

# ==================== 用户管理模块 ====================
@app.route('/admin/users')
@admin_required
def admin_users():
    if 'user_id' not in session or session.get('role_id') != 1:
        return redirect('/show/infer')
    return render_template('ad_users.html')

@app.route('/api/admin/users/list')
@admin_required
def get_admin_users_list():
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute("SELECT id, username, real_name, email, role_id, is_active, last_login FROM sys_user")
    users = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(users)

@app.route('/ad/users/change', methods=['GET'])
@admin_required
def ad_users_change():
    uid = request.args.get('uid')
    if not uid:
        return redirect('/admin/users')

    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute("SELECT * FROM sys_user WHERE id=%s", (uid,))
    data = cursor.fetchone()
    cursor.close()
    conn.close()

    if not data:
        return "用户不存在", 404
    return render_template('ad_users_change.html', data=data)

@app.route('/api/admin/users/update', methods=['POST'])
@admin_required
def admin_users_update():
    data = request.get_json(silent=True) or {}
    if not data.get('id') or str(data.get('role_id')) not in {'1', '2'} or str(data.get('is_active')) not in {'0', '1', 'True', 'False'}:
        return jsonify({'success': False, 'error': 'invalid_user_payload'}), 400
    conn = get_db()
    cursor = conn.cursor()

    try:
        if data.get('password'):
            password_hash = generate_password_hash(data['password'])
            cursor.execute("""
                UPDATE sys_user SET real_name=%s, email=%s, role_id=%s, is_active=%s, password_hash=%s
                WHERE id=%s
            """, (data.get('real_name'), data.get('email'), data['role_id'], data['is_active'], password_hash, data['id']))
        else:
            cursor.execute("""
                UPDATE sys_user SET real_name=%s, email=%s, role_id=%s, is_active=%s
                WHERE id=%s
            """, (data.get('real_name'), data.get('email'), data['role_id'], data['is_active'], data['id']))
        conn.commit()
        add_log(session.get('user_id'), 'user_edit', f'编辑用户 ID: {data["id"]}', 'INFO')
        return jsonify({'success': True})
    except pymysql.MySQLError:
        logger.exception("Unable to update user.")
        return jsonify({'success': False, 'error': 'user_update_failed'}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/admin/users/delete/<int:user_id>', methods=['POST'])
@admin_required
def admin_users_delete(user_id):
    if user_id == session.get('user_id'):
        return jsonify({'success': False, 'error': '不能删除自己'})

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT username FROM sys_user WHERE id=%s", (user_id,))
        result = cursor.fetchone()
        username = result[0] if result else str(user_id)

        cursor.execute("DELETE FROM sys_user WHERE id=%s", (user_id,))
        conn.commit()
        add_log(session.get('user_id'), 'user_delete', f'删除用户: {username}', 'WARNING')
        return jsonify({'success': True})
    except pymysql.MySQLError:
        logger.exception("Unable to delete user %s.", user_id)
        return jsonify({'success': False, 'error': 'user_delete_failed'}), 500
    finally:
        cursor.close()
        conn.close()

# ==================== 系统日志模块 ====================
@app.route('/system/logs')
@admin_required
def system_logs():
    if 'user_id' not in session or session.get('role_id') != 1:
        return redirect('/show/infer')
    return render_template('system_logs.html')

@app.route('/api/logs/list')
@admin_required
def get_logs_list():
    page = max(request.args.get('page', 1, type=int), 1)
    per_page = 20

    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    try:
        offset = (page - 1) * per_page
        cursor.execute("""
            SELECT l.*, u.username
            FROM system_logs l
            LEFT JOIN sys_user u ON l.user_id = u.id
            ORDER BY l.created_at DESC
            LIMIT %s OFFSET %s
        """, (per_page, offset))

        logs = cursor.fetchall()

        cursor.execute("SELECT COUNT(*) as total FROM system_logs")
        total_result = cursor.fetchone()
        total = total_result['total'] if total_result else 0

        cursor.close()
        conn.close()

        return jsonify({
            'logs': logs,
            'total': total,
            'page': page,
            'per_page': per_page
        })

    except Exception as e:
        cursor.close()
        conn.close()
        return jsonify({'logs': [], 'total': 0, 'page': page, 'per_page': per_page})

# ==================== 页面路由 ====================
@app.route('/get/manager')
@admin_required
def get_manager():
    return render_template("manager.html")

@app.route('/get/user')
@login_required
def get_user():
    return render_template("user.html")

@app.route('/get/wrong')
def get_wrong():
    return render_template("wrong.html")

if __name__ == '__main__':
    if app.config["ENABLE_ALERT_CHECKER"]:
        start_alert_checker()
    app.run(
        debug=app.config["FLASK_DEBUG"],
        host=app.config["FLASK_HOST"],
        port=app.config["FLASK_PORT"],
    )
