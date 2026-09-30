import pymysql
import time
import random
import threading
from datetime import datetime

from config import get_db_connection

class TrafficCollector:
    """流量数据采集器"""

    def __init__(self):
        self.running = True
        self.thread = None

    def get_db(self):
        """获取数据库连接"""
        return get_db_connection()

    def collect_device_traffic(self):
        """采集每个设备的流量（模拟SNMP采集）"""
        conn = self.get_db()
        cursor = conn.cursor()

        # 获取所有在线设备
        cursor.execute("SELECT id, device_name FROM mon_device WHERE status=1")
        devices = cursor.fetchall()

        total_in = 0
        total_out = 0
        active_count = 0

        for device_id, device_name in devices:
            # 模拟采集流量数据（实际项目中用SNMP或NetFlow）
            in_rate = round(random.uniform(5, 200), 2)      # 5-200 Mbps
            out_rate = round(random.uniform(3, 150), 2)     # 3-150 Mbps
            packet_rate = random.randint(1000, 50000)       # 包/秒
            tcp_conn = random.randint(10, 2000)             # TCP连接数
            udp_flows = random.randint(5, 500)              # UDP流数

            # 插入实时数据
            cursor.execute("""
                INSERT INTO realtime_traffic
                (device_id, in_rate, out_rate, packet_rate, tcp_connections, udp_flows)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (device_id, in_rate, out_rate, packet_rate, tcp_conn, udp_flows))

            total_in += in_rate
            total_out += out_rate
            active_count += 1

            # 检查是否需要告警
            self.check_threshold(device_id, device_name, in_rate, out_rate)

        # 插入总体统计
        cursor.execute("""
            INSERT INTO traffic_stats (stat_time, total_in_rate, total_out_rate, active_devices)
            VALUES (NOW(), %s, %s, %s)
        """, (total_in, total_out, active_count))

        conn.commit()
        cursor.close()
        conn.close()

    def check_threshold(self, device_id, device_name, in_rate, out_rate):
        """检查阈值告警"""
        conn = self.get_db()
        cursor = conn.cursor()

        # 获取设备阈值设置（从user_config或alert_rule表）
        cursor.execute("""
            SELECT threshold_value FROM alert_rule
            WHERE condition_type='traffic' AND severity='warning'
            LIMIT 1
        """)
        threshold = cursor.fetchone()

        if threshold and in_rate > threshold[0]:
            # 插入告警
            cursor.execute("""
                INSERT INTO alerts (device_id, alert_type, severity, message, current_value, threshold_value)
                VALUES (%s, 'traffic', 'warning', %s, %s, %s)
            """, (device_id, f"{device_name}入流量过高", in_rate, threshold[0]))
            conn.commit()

        cursor.close()
        conn.close()

    def start_collecting(self):
        """启动采集线程"""
        def run():
            while self.running:
                try:
                    self.collect_device_traffic()
                    print(f"[{datetime.now()}] 流量采集完成")
                except Exception as e:
                    print(f"采集错误: {e}")
                time.sleep(10)  # 每10秒采集一次

        self.thread = threading.Thread(target=run)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        self.running = False
