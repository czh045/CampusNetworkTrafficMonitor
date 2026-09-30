CREATE DATABASE IF NOT EXISTS campus_traffic
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE campus_traffic;

CREATE TABLE IF NOT EXISTS sys_user (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  username VARCHAR(64) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  email VARCHAR(254) NOT NULL,
  real_name VARCHAR(100) NULL,
  role_id TINYINT UNSIGNED NOT NULL DEFAULT 2,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  last_login DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_sys_user_username (username),
  UNIQUE KEY uq_sys_user_email (email)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS mon_device (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  device_name VARCHAR(100) NOT NULL,
  ip_address VARCHAR(45) NOT NULL,
  device_type VARCHAR(64) NULL,
  snmp_community VARCHAR(128) NULL,
  status TINYINT(1) NOT NULL DEFAULT 1,
  last_check DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_mon_device_ip (ip_address)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS realtime_traffic (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  device_id BIGINT UNSIGNED NOT NULL,
  in_rate DECIMAL(12,2) NOT NULL DEFAULT 0,
  out_rate DECIMAL(12,2) NOT NULL DEFAULT 0,
  packet_rate INT UNSIGNED NOT NULL DEFAULT 0,
  tcp_connections INT UNSIGNED NOT NULL DEFAULT 0,
  udp_flows INT UNSIGNED NOT NULL DEFAULT 0,
  packet_loss DECIMAL(6,2) NOT NULL DEFAULT 0,
  timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_realtime_traffic_device_time (device_id, timestamp),
  KEY idx_realtime_traffic_timestamp (timestamp),
  CONSTRAINT fk_realtime_traffic_device
    FOREIGN KEY (device_id) REFERENCES mon_device(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS traffic_stats (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  stat_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  total_in_rate DECIMAL(12,2) NOT NULL DEFAULT 0,
  total_out_rate DECIMAL(12,2) NOT NULL DEFAULT 0,
  active_devices INT UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY idx_traffic_stats_time (stat_time)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS alerts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  device_id BIGINT UNSIGNED NULL,
  alert_type VARCHAR(64) NOT NULL,
  severity ENUM('info', 'warning', 'critical') NOT NULL DEFAULT 'warning',
  title VARCHAR(255) NOT NULL,
  message TEXT NOT NULL,
  current_value DECIMAL(14,2) NULL,
  threshold_value DECIMAL(14,2) NULL,
  status ENUM('active', 'acknowledged', 'resolved') NOT NULL DEFAULT 'active',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at DATETIME NULL,
  last_notified_at DATETIME NULL,
  notify_count INT UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY idx_alerts_status_created (status, created_at),
  KEY idx_alerts_device_type (device_id, alert_type),
  CONSTRAINT fk_alerts_device
    FOREIGN KEY (device_id) REFERENCES mon_device(id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS alert_rule (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  condition_type VARCHAR(64) NOT NULL,
  severity ENUM('info', 'warning', 'critical') NOT NULL DEFAULT 'warning',
  threshold_value DECIMAL(14,2) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_alert_rule_condition_severity (condition_type, severity)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS system_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id BIGINT UNSIGNED NULL,
  log_level VARCHAR(16) NOT NULL DEFAULT 'INFO',
  action_type VARCHAR(64) NOT NULL,
  message TEXT NOT NULL,
  ip_address VARCHAR(45) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_system_logs_created (created_at),
  CONSTRAINT fk_system_logs_user
    FOREIGN KEY (user_id) REFERENCES sys_user(id) ON DELETE SET NULL
) ENGINE=InnoDB;

INSERT IGNORE INTO alert_rule (condition_type, severity, threshold_value)
VALUES ('traffic', 'warning', 80.00);
