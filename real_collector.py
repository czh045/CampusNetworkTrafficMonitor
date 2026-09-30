import pymysql
import time
import threading
from datetime import datetime
from pysnmp.hlapi import *

from config import get_db_connection

class SNMPTrafficCollector:
    """真实SNMP流量采集器"""

    def __init__(self):
        self.running = True

    def get_db(self):
        return get_db_connection()

    def snmp_get(self, ip, community, oid):
        """SNMP GET请求"""
        iterator = getCmd(
            SnmpEngine(),
            CommunityData(community),
            UdpTransportTarget((ip, 161), timeout=3, retries=1),
            ContextData(),
            ObjectType(ObjectIdentity(oid))
        )

        errorIndication, errorStatus, errorIndex, varBinds = next(iterator)

        if errorIndication or errorStatus:
            return None
        for varBind in varBinds:
            return int(varBind[1])
        return None

    def collect_device(self, device):
        """采集单个设备流量"""
        device_id = device[0]
        device_name = device[1]
        ip = device[2]
        community = device[3]

        # OID定义
        OID_IN = '1.3.6.1.2.1.2.2.1.10.1'   # 入流量字节数
        OID_OUT = '1.3.6.1.2.1.2.2.1.16.1'  # 出流量字节数

        in_bytes = self.snmp_get(ip, community, OID_IN)
        out_bytes = self.snmp_get(ip, community, OID_OUT)

        if in_bytes is not None and out_bytes is not None:
            # 计算速率需要两次采集的差值
            return {
                'device_id': device_id,
                'in_bytes': in_bytes,
                'out_bytes': out_bytes
            }
        return None

    def run(self):
        """运行采集"""
        conn = self.get_db()
        cursor = conn.cursor()

        # 获取启用SNMP的设备
        cursor.execute("""
            SELECT id, device_name, ip_address, snmp_community
            FROM mon_device
            WHERE status=1 AND snmp_community IS NOT NULL
        """)
        devices = cursor.fetchall()
        cursor.close()
        conn.close()

        # 采集第一次（基准值）
        base_data = {}
        for device in devices:
            data = self.collect_device(device)
            if data:
                base_data[device[0]] = data
        time.sleep(60)  # 等待60秒

        # 采集第二次，计算速率
        conn = self.get_db()
        cursor = conn.cursor()
        for device in devices:
            data = self.collect_device(device)
            if data and device[0] in base_data:
                base = base_data[device[0]]
                in_rate = (data['in_bytes'] - base['in_bytes']) * 8 / 1e6 / 60  # Mbps
                out_rate = (data['out_bytes'] - base['out_bytes']) * 8 / 1e6 / 60

                cursor.execute("""
                    INSERT INTO realtime_traffic (device_id, in_rate, out_rate, timestamp)
                    VALUES (%s, %s, %s, NOW())
                """, (device[0], max(0, in_rate), max(0, out_rate)))

        conn.commit()
        cursor.close()
        conn.close()
        print(f"[{datetime.now()}] SNMP采集完成")

if __name__ == '__main__':
    collector = SNMPTrafficCollector()
    while True:
        collector.run()
        time.sleep(60)  # 每分钟采集一次
